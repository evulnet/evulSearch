"""Secure search proxy — the Brave API key lives only here, never in the browser.

Requests (and sanitized results) are cached server-side in SQLite; see
config.example.json for cache semantics (cache_hours: -1 off, 0 forever,
>0 hours; default 168 = one week).
"""
import html
import json as jsonlib
import os
import sqlite3
import time
from urllib.parse import quote_plus, urlparse

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

# --- Config file (config.json next to app.py; EVUL_CONFIG_PATH overrides for tests) ---
CONFIG_PATH = os.environ.get("EVUL_CONFIG_PATH", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config.json"))

# Provider registry (used by config cleaning, priority and quotas).
PROVIDER_NAMES = {"brave", "serpapi", "brightdata", "searxng"}
# Engines the UI knows about and the provider order used when config.json
# does not override it via "engine_priority". An EMPTY list in config means
# "engine disabled"; providers may also be reordered per engine.
ENGINE_DEFAULT_ORDER = {
    "brave": ["brave"],
    "google": ["serpapi", "brightdata"],
    "bing": ["brightdata"],
    "searxng": ["searxng"],
}


def _clean_priority(v):
    out = {}
    if isinstance(v, dict):
        for eng, lst in v.items():
            if eng in ENGINE_DEFAULT_ORDER and isinstance(lst, list):
                allowed = ENGINE_DEFAULT_ORDER[eng]
                # keep admin's order; drop unknown providers, ignore anything
                # that could never serve this engine
                out[eng] = [p for p in lst if isinstance(p, str) and p in allowed]
    return out


def _clean_quotas(v):
    out = {}
    if isinstance(v, dict):
        for p, q in v.items():
            if p not in PROVIDER_NAMES or not isinstance(q, dict):
                continue
            cleaned = {}
            for key in ("per_day", "per_month"):
                try:
                    n = int(q.get(key))
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    cleaned[key] = n
            if cleaned:
                out[p] = cleaned
    return out


def _place_img(r):
    """First picture (Brave CDN), falling back to the thumbnail.

    Live Brave returns `pictures` as {"results": [...]} and some clients/tests
    use a list of such dicts — accept both shapes.
    """
    pics = r.get("pictures")
    item = None
    if isinstance(pics, list) and pics:
        item = pics[0]
    elif isinstance(pics, dict):
        item = pics
    if isinstance(item, dict):
        inner = item.get("results") or []
        if inner and isinstance(inner[0], dict):
            src = inner[0].get("src", "")
            if src:
                return src
    thumb = r.get("thumbnail")
    return thumb.get("src", "") if isinstance(thumb, dict) else ""


def _today_hours(oh):
    """Summarize opening_hours as 'Saturday 12:30-00:30' if Brave provided it."""
    try:
        day = (oh or {}).get("current_day", [None])[0]
        if day and day.get("opens"):
            return f"{day.get('full_name', '')} {day['opens']}-{day.get('closes', '')}".strip()
    except (AttributeError, IndexError, TypeError):
        pass
    return ""


def _parse_hours(value, default):
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return default
    if hours < 0 and hours != -1:
        hours = -1  # any negative other than -1 means disabled
    return hours


def load_config():
    cfg = {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = jsonlib.load(f)
    except (OSError, ValueError):
        pass  # missing/invalid config -> defaults
    return {
        "cache_hours": _parse_hours(cfg.get("cache_hours", 168), 168),
        # News changes fast; default to no caching, configurable separately.
        "cache_hours_news": _parse_hours(cfg.get("cache_hours_news", -1), -1),
        "cache_db": cfg.get("cache_db") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "evulsearch.db"),
        # Third-party map tiles: served by default (double opt-OUT: set
        # allow_map_embed=false on the server AND/OR uncheck the user cog).
        "allow_map_embed": bool(cfg.get("allow_map_embed", True)),
        # Admin levers: per-engine provider priority (cost vs richness) and
        # per-provider call quotas (UTC day/month, counted in the SQLite db).
        "engine_priority": _clean_priority(cfg.get("engine_priority")),
        "provider_quotas": _clean_quotas(cfg.get("provider_quotas")),
    }


CONFIG = load_config()


def cache_hours_for(kind):
    if kind == "news":
        return CONFIG["cache_hours_news"]
    return CONFIG["cache_hours"]

BRAVE_BASE = os.environ.get(
    "BRAVE_BASE", "https://api.search.brave.com/res/v1"
)  # override only for tests (mock Brave)
ENDPOINTS = {
    "web": BRAVE_BASE + "/web/search",
    "news": BRAVE_BASE + "/news/search",
    "images": BRAVE_BASE + "/images/search",
    "places": BRAVE_BASE + "/local/place_search",
}
API_KEY = os.environ.get("BRAVE_API_KEY", "")
# Answers is a separate Brave product with its own subscription token.
ANSWERS_KEY = os.environ.get("BRAVE_ANSWERS_KEY", "")
ANSWERS_URL = BRAVE_BASE + "/chat/completions"  # OpenAI-compatible endpoint

# Optional second Answers backend: any OpenAI-compliant /v1 chat server
# (vLLM, Ollama, llama.cpp, hosted). http:// is allowed for LAN boxes but
# sends queries in PLAINTEXT - see README caution before enabling.
LOCAL_CHAT_URL = os.environ.get("LOCAL_CHAT_BASE_URL", "").rstrip("/")
LOCAL_CHAT_KEY = os.environ.get("LOCAL_CHAT_API_KEY", "")
LOCAL_CHAT_MODEL = os.environ.get("LOCAL_CHAT_MODEL", "")  # empty = auto-detect
LOCAL_CHAT_LABEL = (os.environ.get("LOCAL_CHAT_LABEL") or "Local")[:24]

def answers_backends():
    b = []
    if ANSWERS_KEY:
        b.append({"id": "brave", "label": "Brave"})
    if LOCAL_CHAT_URL:
        b.append({"id": "local", "label": LOCAL_CHAT_LABEL})
    return b


_local_model_cache = {"model": ""}

def _local_chat_target():
    """Resolve (base_url, model); model auto-detects via GET {base}/models
    (first id) unless LOCAL_CHAT_MODEL pins one. Detection cached per process.
    """
    if LOCAL_CHAT_MODEL:
        return LOCAL_CHAT_URL, LOCAL_CHAT_MODEL, None
    if _local_model_cache["model"]:
        return LOCAL_CHAT_URL, _local_model_cache["model"], None
    headers = {"Accept": "application/json"}
    if LOCAL_CHAT_KEY:
        headers["Authorization"] = f"Bearer {LOCAL_CHAT_KEY}"
    try:
        resp = requests.get(LOCAL_CHAT_URL + "/models", headers=headers, timeout=15)
        j = resp.json()
        rows = j.get("data", []) if isinstance(j, dict) else j
        ids = [m["id"] for m in rows
               if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        ids = []
    if not ids:
        return None, None, f"{LOCAL_CHAT_LABEL}: no model available (set LOCAL_CHAT_MODEL)."
    _local_model_cache["model"] = ids[0]
    return LOCAL_CHAT_URL, ids[0], None

# Bright Data SERP API: optional Google/Bing backend. Capability matrix below
# (BD_CAPS); Answers remain Brave/local only.
BD_KEY = os.environ.get("BRIGHTDATA_SERP_API_KEY", "")
BD_ZONE = os.environ.get("BRIGHTDATA_SERP_ZONE", "")
# URL overridable so tests can point at the mock server.
BD_URL = os.environ.get("BRIGHTDATA_API_URL", "https://api.brightdata.com/request")
BD_ENGINES = ("google", "bing")
BD_GOOGLE_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
# What each engine can actually serve (drives /api/config and the UI).
# Shapes verified against a live zone 2026-10: google web/news/images via
# organic|news|images arrays, places via snack_pack (no coordinates -> no
# pins). Bing is parsed web only.
BD_CAPS = {"google": ["web", "news", "images", "places"], "bing": ["web"]}
BRAVE_CAPS = ["web", "news", "images", "places", "answers"]

# SerpApi: the "OpenAI of SERP" idea; richer Places (coordinates, phones,
# opening hours) than Bright Data's lcl scrape, so it wins the Google pill
# when both are configured. Google only for now — SerpApi's Bing shapes are
# unmapped here, so we do not advertise them.
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
# URL overridable so tests can point at the mock server.
SERPAPI_URL = os.environ.get("SERPAPI_SEARCH_URL", "https://serpapi.com/search.json")
SERPAPI_CAPS = {"google": ["web", "news", "images", "places"]}

# SearXNG: self-hosted metasearch (no vendor, no quota). JSON API must be
# enabled in its settings.yml (search.formats: [json]). No Places support.
SEARXNG_URL = os.environ.get("SEARXNG_URL", "").rstrip("/")
SEARX_CAPS = ["web", "news", "images"]

def _provider_caps(prov, engine):
    if prov == "brave":
        return BRAVE_CAPS if engine == "brave" else None
    if prov == "searxng":
        return list(SEARX_CAPS) if engine == "searxng" else None
    if prov == "serpapi":
        return SERPAPI_CAPS.get(engine)
    if prov == "brightdata":
        return BD_CAPS.get(engine)
    return None


def _provider_configured(prov):
    return {"brave": bool(API_KEY),
            "serpapi": bool(SERPAPI_KEY),
            "brightdata": bool(BD_KEY and BD_ZONE),
            "searxng": bool(SEARXNG_URL)}.get(prov, False)


def engine_providers(engine):
    """Configured, enabled providers for an engine id, best-first.

    Order comes from config.json `engine_priority[engine]` when present (an
    empty list disables the engine entirely), else ENGINE_DEFAULT_ORDER.
    Providers the admin left out of a non-empty list are treated as disabled
    for that engine - that is how you keep a metered API off the hot path.
    """
    default = ENGINE_DEFAULT_ORDER.get(engine, [])
    prio = CONFIG.get("engine_priority") or {}
    order = prio[engine] if engine in prio else default
    out = []
    for prov in order:
        caps = _provider_caps(prov, engine)
        if caps and _provider_configured(prov):
            out.append((prov, caps))
    return out


def engines_available():
    """Engines the UI may offer: id -> union of enabled provider tabs.

    Quotas are deliberately NOT consulted here (they reset on their own);
    a quota-exhausted provider falls through to the next one at request time.
    """
    e = []
    for eng in ("brave",) + BD_ENGINES + ("searxng",):
        tabs = []
        for _prov, caps in engine_providers(eng):
            tabs += [t for t in caps if t not in tabs]
        if tabs:
            entry = {"id": eng, "tabs": tabs}
            if eng == "searxng":
                entry["label"] = "SearXNG"
            e.append(entry)
    return e
PORT = int(os.environ.get("PORT", 5000))
HOST = os.environ.get("HOST", "127.0.0.1")

ALLOWED_SAFESEARCH = {"strict", "moderate", "off"}
ALLOWED_COUNTS = {5, 10, 20}
ALLOWED_TABS = set(ENDPOINTS)
# UI value -> Brave freshness code
FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}

app = Flask(__name__, static_folder="static")


# --- Query/result cache (SQLite, lazy purge, per-kind TTL) ---
def _db():
    conn = sqlite3.connect(CONFIG["cache_db"])
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY,"
            " payload TEXT NOT NULL, created REAL NOT NULL, ttl_hours REAL NOT NULL)")
    except sqlite3.OperationalError:
        pass  # table exists
    # One-shot migration: pre-TTL schema held disposable data — drop it.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cache)")]
    if "ttl_hours" not in cols:
        conn.execute("DROP TABLE cache")
        conn.execute(
            "CREATE TABLE cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL,"
            " created REAL NOT NULL, ttl_hours REAL NOT NULL)")
    return conn


def _purge(conn):
    conn.execute(
        "DELETE FROM cache WHERE ttl_hours > 0 AND created < ? - ttl_hours * 3600",
        (time.time(),))


def cache_get(key, hours):
    if hours == -1:
        return None
    with _db() as conn:
        _purge(conn)
        row = conn.execute(
            "SELECT payload, created, ttl_hours FROM cache WHERE key = ?",
            (key,)).fetchone()
    if not row:
        return None
    payload, created, ttl = row
    if ttl > 0 and time.time() - created > ttl * 3600:
        return None  # not yet purged but stale
    return jsonlib.loads(payload)


def cache_set(key, payload, hours):
    if hours == -1:
        return
    with _db() as conn:
        _purge(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, payload, created, ttl_hours)"
            " VALUES (?, ?, ?, ?)",
            (key, jsonlib.dumps(payload), time.time(), hours))


def cache_key(prefix, parts):
    return prefix + "|" + "|".join(str(p) for p in parts)


# --- Provider usage counters (for quotas; counts only, never query text) ---
def _usage_db():
    conn = sqlite3.connect(CONFIG["cache_db"])
    conn.execute(
        "CREATE TABLE IF NOT EXISTS provider_usage (provider TEXT NOT NULL,"
        " ymd TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (provider, ymd))")
    return conn


def quota_blocked_reason(prov):
    """None if the provider may be called now, else a short human reason."""
    quota = (CONFIG.get("provider_quotas") or {}).get(prov)
    if not quota:
        return None
    today = time.strftime("%Y-%m-%d", time.gmtime())  # UTC resets
    month = today[:7]
    try:
        conn = _usage_db()
    except sqlite3.Error:
        return None  # never block traffic because bookkeeping failed
    try:
        day = conn.execute(
            "SELECT calls FROM provider_usage WHERE provider=? AND ymd=?",
            (prov, today)).fetchone()
        day_used = day[0] if day else 0
        if quota.get("per_day") and day_used >= quota["per_day"]:
            return "daily quota reached"
        if quota.get("per_month"):
            row = conn.execute(
                "SELECT COALESCE(SUM(calls),0) FROM provider_usage"
                " WHERE provider=? AND ymd LIKE ?",
                (prov, month + "%")).fetchone()
            if row[0] >= quota["per_month"]:
                return "monthly quota reached"
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return None


def usage_bump(prov):
    if not (CONFIG.get("provider_quotas") or {}).get(prov):
        return  # no quota configured -> no bookkeeping cost
    today = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        conn = _usage_db()
        conn.execute(
            "INSERT INTO provider_usage (provider, ymd, calls) VALUES (?, ?, 1)"
            " ON CONFLICT(provider, ymd) DO UPDATE SET calls = calls + 1",
            (prov, today))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def _brightdata_serp(query, engine, tab, count, safesearch, freshness):
    """Results via Bright Data SERP. Returns (items, error); one is non-None.

    URL note: Bright Data requires q= to be the FIRST query argument.
    parsed_light is web-only (organic+top stories); news/places need full JSON.
    """
    q = quote_plus(query)
    if engine == "google":
        url = f"https://www.google.com/search?q={q}&hl=en&gl=us"
        if tab == "news":
            url += "&tbm=nws"
        elif tab == "places":
            url += "&tbm=lcl"
        elif tab == "images":
            url += "&tbm=isch"
        elif count == 20:
            # num=10 (Google default) makes Bright Data return an unusable body;
            # only request the widened page explicitly. Verified live 2026-10.
            url += "&num=20"
        # Google's safe flag is active|off; send off explicitly so the UI
        # promise matches the SERP (omitting it would leave moderate filtering).
        if safesearch == "strict":
            url += "&safe=active"
        elif safesearch == "off":
            url += "&safe=off"
        if freshness in BD_GOOGLE_TBS and tab in ("web", "news"):
            url += "&tbs=" + BD_GOOGLE_TBS[freshness]
        data_format = "parsed_light" if tab == "web" else "parsed"
    else:  # bing — web only; freshness syntax brittle across regions, unmapped
        url = f"https://www.bing.com/search?q={q}&count={count}"
        if safesearch == "strict":
            url += "&adlt=strict"
        elif safesearch == "off":
            url += "&adlt=off"
        data_format = "parsed_bing"
    try:
        resp = requests.post(
            BD_URL,
            headers={"Authorization": f"Bearer {BD_KEY}",
                     "Content-Type": "application/json"},
            json={"zone": BD_ZONE, "url": url,
                  "format": "raw", "data_format": data_format},
            timeout=120,  # cold BD scrapes: web ~5s, isch ~30s, lcl can hit 60-90s (base64 payloads)
        )
    except requests.RequestException:
        return None, "Could not reach Bright Data SERP API."
    if resp.status_code != 200:
        return None, f"Bright Data error (HTTP {resp.status_code})."
    try:
        data = resp.json()
    except ValueError:
        return None, "Bad response from Bright Data SERP API."
    if tab == "news":
        items = data.get("news") or (data.get("top_stories") or {}).get("items") or []
    elif tab == "places":
        items = data.get("snack_pack") or []
    elif tab == "images":
        items = data.get("images") or []
    else:
        items = data.get("organic") or data.get("results") or []
    return items[:count], None


def _bd_map_images(items):
    """Google image SERP -> our Images shape. Only original_image is a usable
    URL (BD's `image`/`image_base64` are inline base64; `link` is a Google
    redirect). Stripped server-side so multi-MB blobs never hit the client.
    """
    out = []
    for it in items:
        origin = it.get("original_image") or ""
        if not origin:
            continue
        out.append({
            "title": html.unescape(it.get("image_alt") or it.get("title") or ""),
            "url": origin,
            "source": urlparse(origin).netloc,
            "thumb": origin,   # no thumbnail URL available from BD
            "origin": origin,
        })
    return out


def _bd_map_places(items):
    """Google local pack -> our Places shape (verified against live zone).

    No coordinates/phones available; images are raw base64 megabytes and are
    deliberately stripped. `cid` yields the canonical Google Maps URL.
    """
    out = []
    for it in items:
        name = html.unescape(it.get("name") or "")
        if not name:
            continue
        cid = it.get("cid") or ""
        bits = []
        if it.get("rating"):
            cnt = it.get("reviews_cnt")
            bits.append(f"{it['rating']}\u2605" + (f" ({cnt} reviews)" if cnt else ""))
        if it.get("work_status_details"):
            bits.append(str(it["work_status_details"]))
        out.append({
            "title": name,
            "url": f"https://www.google.com/maps?cid={cid}" if cid else "",
            "address": html.unescape(it.get("address") or ""),
            "phone": "",
            "coordinates": [],
            "categories": html.unescape(it.get("type") or ""),
            "opening": "",
            "description": " \u00b7 ".join(bits),
            "img": "",
        })
    return out


def _bd_map(items, tab):
    """Map Bright Data items to our normalized web/news result shape."""
    out = []
    for it in items:
        link = it.get("link") or it.get("url") or ""
        if not link:
            continue
        src = it.get("display_link") or it.get("source") or ""
        # Bing parsing leaks breadcrumb/UI text into display_link; trust it
        # only when it looks host-like, else fall back to the link's netloc.
        if " " in src or "›" in src or "»" in src:
            src = urlparse(link).netloc
        out.append({
            "title": html.unescape(it.get("title") or ""),
            "url": link,
            "description": html.unescape(it.get("description") or it.get("excerpt") or ""),
            "age": it.get("time") or it.get("updated") or "",
            "source": src or urlparse(link).netloc,
        })
    return out


def _serpapi_search(query, engine, tab, count, safesearch, freshness):
    """Results via SerpApi. Returns (items, error); one is non-None."""
    if engine != "google":
        return None, "SerpApi provider supports Google only."
    params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY,
              "hl": "en", "gl": "us", "num": min(count, 20)}
    if tab == "news":
        params["engine"] = "google_news"
    elif tab == "images":
        params["engine"] = "google_images"
    elif tab == "places":
        # Places ride on a normal google search (verified live 2026-10);
        # the separate google_locations product is not on this plan.
        params["fetch_place_results"] = "true"
    if safesearch == "strict":
        params["safe"] = "active"
    elif safesearch == "off":
        params["safe"] = "off"
    if freshness in ("day", "week", "month", "year") and tab in ("web", "news"):
        params["tbs"] = {"day": "qdr:d", "week": "qdr:w",
                         "month": "qdr:m", "year": "qdr:y"}[freshness]
    try:
        resp = requests.get(SERPAPI_URL, params=params, timeout=60)
    except requests.RequestException:
        return None, "Could not reach SerpApi."
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200:
        # SerpApi error bodies can quote the request; relay only the status.
        return None, f"SerpApi error (HTTP {resp.status_code})."
    if tab == "places":
        lr = data.get("local_results") or {}
        items = lr.get("places") if isinstance(lr, dict) else lr
        items = items or []
    else:
        key = {"web": "organic_results", "news": "news_results",
               "images": "images_results"}[tab]  # verified live 2026-10
        items = data.get(key) or []
        if tab == "news" and not items:  # google_news also uses organic_results
            items = data.get("organic_results") or []
    return items[:count], None


def _serpapi_map(items, tab):
    """SerpApi items -> our normalized shapes (web/news/images/places)."""
    out = []
    for it in items:
        if tab == "places":
            name = html.unescape(str(it.get("title") or ""))
            if not name:
                continue
            gps = it.get("gps_coordinates") or {}
            lat, lon = gps.get("latitude"), gps.get("longitude")
            links = it.get("links") or {}
            bits = []
            if it.get("rating"):
                bits.append(f"{it['rating']}\u2605")
            if it.get("reviews"):
                bits.append(f"{it['reviews']} reviews")
            if it.get("hours"):
                bits.append(str(it["hours"]))
            out.append({
                "title": name,
                "url": links.get("website") or "",
                "address": html.unescape(str(it.get("address") or "")),
                "phone": str(it.get("phone") or ""),
                "coordinates": [lat, lon] if lat is not None and lon is not None else [],
                "categories": html.unescape(str(it.get("type") or "")),
                "opening": " \u00b7 ".join(bits),
                "description": "",
                # SerpApi's own `thumbnail` is a short-lived cached URL; the
                # googleusercontent `thumbnail_large` is hotlinkable.
                "img": it.get("thumbnail_large") or "",
            })
            continue
        link = it.get("link") or it.get("url") or ""
        if not link:
            continue
        src = it.get("source") or it.get("displayed_link") or ""
        src = str(src)
        if " " in src or "\u203a" in src or "\u00bb" in src:
            src = urlparse(link).netloc
        row = {
            "title": html.unescape(it.get("title") or ""),
            "url": link,
            "description": html.unescape(it.get("snippet") or it.get("description") or ""),
            "age": it.get("date") or it.get("new_date") or it.get("published_date") or "",
            "source": src or urlparse(link).netloc,
        }
        if tab == "images":
            # google_images shape (verified live 2026-10): `original` is the
            # direct image URL, `thumbnail` a gstatic proxy URL.
            origin = (it.get("original") or it.get("thumbnail_large")
                      or it.get("thumbnail") or link)
            row = {
                "title": html.unescape(it.get("title") or ""),
                "url": link, "source": row["source"],
                "thumb": it.get("thumbnail") or origin,
                "origin": origin,
            }
        out.append(row)
    return out


SEARX_TBS = {"day": "day", "week": "week", "month": "month", "year": "year"}


def _searx_search(query, tab, count, safesearch, freshness):
    """Results from a self-hosted SearXNG (JSON API). (items, error)."""
    cats = {"web": "general", "news": "news", "images": "images"}[tab]
    params = {"q": query, "format": "json", "categories": cats, "pageno": 1,
              "safesearch": {"off": 0, "moderate": 1, "strict": 2}[safesearch]}
    if freshness in SEARX_TBS:
        params["time_range"] = SEARX_TBS[freshness]
    try:
        resp = requests.get(SEARXNG_URL + "/search", params=params,
                            headers={"Accept": "application/json"}, timeout=30)
    except requests.RequestException:
        return None, "Could not reach SearXNG."
    if resp.status_code == 403:
        return None, ("SearXNG refused JSON (enable formats: [json] "
                      "in its settings.yml).")
    if resp.status_code != 200:
        return None, f"SearXNG error (HTTP {resp.status_code})."
    try:
        data = resp.json()
    except ValueError:
        return None, "Bad response from SearXNG."
    return (data.get("results") or [])[:count], None


def _searx_map(items, tab):
    out = []
    for it in items:
        link = it.get("url") or ""
        if not link:
            continue
        if tab == "images":
            origin = it.get("img_src") or ""
            if not origin:
                continue
            out.append({
                "title": html.unescape(it.get("title") or ""),
                "url": link, "source": urlparse(link).netloc,
                "thumb": it.get("thumbnail") or origin,
                "origin": origin,
            })
            continue
        out.append({
            "title": html.unescape(it.get("title") or ""),
            "url": link,
            "description": html.unescape(it.get("content") or ""),
            "age": it.get("published_date") or "",
            "source": it.get("source") or urlparse(link).netloc,
        })
    return out


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/search")
def search():
    engine = request.args.get("engine", "brave")
    if engine not in ("brave", "searxng") + BD_ENGINES:
        engine = "brave"
    # Per-engine availability (missing key OR disabled via engine_priority)
    # is decided in one place below, together with quota fall-through.

    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify(error="Missing query."), 400
    if len(query) > 400:
        return jsonify(error="Query too long."), 400

    safesearch = request.args.get("safesearch", "moderate")
    if safesearch not in ALLOWED_SAFESEARCH:
        safesearch = "moderate"
    try:
        count = int(request.args.get("count", 10))
    except ValueError:
        count = 10
    if count not in ALLOWED_COUNTS:
        count = 10

    tab = request.args.get("tab", "web")
    if tab not in ALLOWED_TABS:
        tab = "web"

    params = {"q": query, "count": count}
    if tab == "places":
        # Optional free-text location override (takes precedence over lat/lon client-side too).
        loc = (request.args.get("loc") or "").strip()[:200]
        loc = " ".join(loc.split())  # no newlines/tabs
        if loc:
            params["q"] = f"{query} in {loc}"
        # Optional caller-supplied coordinates (from browser geolocation).
        try:
            lat = float(request.args.get("lat", ""))
            lon = float(request.args.get("lon", ""))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                params["latitude"] = lat
                params["longitude"] = lon
        except ValueError:
            pass  # no/invalid coords: plain text places query
    # Images endpoint accepts only off|strict (default strict); web|news accept off|moderate|strict.
    if tab == "images":
        params["safesearch"] = "off" if safesearch == "off" else "strict"
    else:
        params["safesearch"] = safesearch
    if tab == "web":  # only documented for web search
        params["spellcheck"] = "false" if request.args.get("spellcheck") == "false" else "true"
    freshness = FRESHNESS.get(request.args.get("freshness", ""))
    if freshness:
        params["freshness"] = freshness

    freshness_raw = request.args.get("freshness", "")

    # Resolve the provider that will actually serve this request:
    # admin priority order, first one that (a) supports the tab and
    # (b) is under its quota. Quota-exhausted providers fall through, so a
    # metered API degrades to the unmetered one instead of failing.
    provs = engine_providers(engine)
    if not provs:
        return jsonify(error=f"{engine.capitalize()} is not configured on this server"
                             " (or disabled via engine_priority)."), 503
    covering = [p for p, caps in provs if tab in caps]
    if not covering:
        return jsonify(error=f"{engine.capitalize()} does not support the "
                             f"{tab.capitalize()} tab on this server."), 400
    provider = next((p for p in covering if quota_blocked_reason(p) is None), None)
    if provider is None:
        reasons = ", ".join(f"{p} ({quota_blocked_reason(p)})" for p in covering)
        return jsonify(error=f"Search providers exhausted: {reasons}."), 429
    usage_bump(provider)

    ckey = cache_key("search", sorted(params.items())
                     + [("engine", engine), ("provider", provider)])
    hours = cache_hours_for(tab)
    hit = cache_get(ckey, hours)
    if hit is not None:
        return jsonify({**hit, "cached": True})

    if provider != "brave":
        # Places loc override mutates params["q"] upstream; honor it for all providers.
        qparam = params.get("q", query)
        if provider == "serpapi":
            items, err = _serpapi_search(qparam, engine, tab, count, safesearch, freshness_raw)
        elif provider == "searxng":
            items, err = _searx_search(qparam, tab, count, safesearch, freshness_raw)
        else:
            items, err = _brightdata_serp(qparam, engine, tab, count, safesearch, freshness_raw)
        if err:
            return jsonify(error=err), 502
        if provider == "serpapi":
            results = _serpapi_map(items, tab)
        elif provider == "searxng":
            results = _searx_map(items, tab)
        elif tab == "places":
            results = _bd_map_places(items)
        elif tab == "images":
            results = _bd_map_images(items)
        else:
            results = _bd_map(items, tab)
        payload = {"results": results, "tab": tab, "engine": engine}
        cache_set(ckey, payload, hours)
        return jsonify(payload)

    try:
        resp = requests.get(
            ENDPOINTS[tab],
            params=params,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": API_KEY,
            },
            timeout=10,
        )
    except requests.RequestException:
        return jsonify(error="Could not reach Brave Search."), 502

    if resp.status_code != 200:
        # Do not relay Brave's body verbatim; only the status is safe to show.
        return jsonify(error=f"Brave Search error (HTTP {resp.status_code})."), 502

    try:
        data = resp.json()
    except ValueError:
        return jsonify(error="Bad response from Brave Search."), 502

    if tab == "news":
        items = data.get("results") or []
    elif tab == "images":
        items = data.get("results") or []
    elif tab == "places":
        items = data.get("results") or []
    else:
        web = data.get("web") or data.get("results", {}).get("web") or {}
        items = web.get("results", [])

    if tab == "images":
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "source": r.get("source", ""),
                "thumb": (r.get("thumbnail") or {}).get("src", ""),
                "origin": (r.get("properties") or {}).get("url", ""),
            }
            for r in items
            if (r.get("thumbnail") or {}).get("src")
        ]
    elif tab == "places":
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "address": (r.get("postal_address") or {}).get("displayAddress", ""),
                "phone": (r.get("contact") or {}).get("telephone", ""),
                "coordinates": r.get("coordinates") or [],
                "categories": ", ".join(r.get("categories") or []),
                "opening": _today_hours(r.get("opening_hours")),
                "description": r.get("description", ""),
                "img": _place_img(r),
            }
            for r in items
        ]
    else:
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
                "age": r.get("age", ""),
            }
            for r in items
        ]
    payload = {"results": results, "tab": tab}
    if tab == "web":
        web = data.get("web") or data.get("results", {}).get("web") or {}
        altered = web.get("altered_query")
        if altered and altered != query:
            payload["altered_query"] = altered
    cache_set(ckey, payload, hours)
    return jsonify(payload)


@app.get("/api/config")
def config():
    # Capability flags for the frontend (no secrets).
    return jsonify(answers_enabled=bool(answers_backends()),
                   answers_backends=answers_backends(),
                   map_embed_allowed=CONFIG.get("allow_map_embed", True),
                   engines=engines_available())


@app.post("/api/answer")
def answer():
    body = request.get_json(silent=True) or {}
    engine = body.get("engine", "brave")
    if engine == "local":
        if not LOCAL_CHAT_URL:
            return jsonify(error="Local answers are not configured on this server."), 503
    elif not ANSWERS_KEY:
        return jsonify(error="Answers are not configured on this server (missing BRAVE_ANSWERS_KEY)."), 503

    query = body.get("q", "").strip()
    if not query:
        return jsonify(error="Missing query."), 400
    if len(query) > 400:
        return jsonify(error="Query too long."), 400

    payload = {
        "stream": False,
        "messages": [{"role": "user", "content": query}],
    }
    freshness = FRESHNESS.get(body.get("freshness", ""))
    if freshness and engine != "local":
        payload["freshness"] = freshness  # Brave extension (verified live); local ignores

    ckey = cache_key("ans", [engine, query, payload.get("freshness", "")])
    hours = cache_hours_for("answers")
    hit = cache_get(ckey, hours)
    if hit is not None:
        return jsonify({**hit, "cached": True})

    if engine == "local":
        target, model, lerr = _local_chat_target()
        if lerr:
            return jsonify(error=lerr), 502
        payload["model"] = model
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if LOCAL_CHAT_KEY:
            headers["Authorization"] = f"Bearer {LOCAL_CHAT_KEY}"
        endpoint, timeout, who = target + "/chat/completions", 90, LOCAL_CHAT_LABEL
    else:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
            "x-subscription-token": ANSWERS_KEY,
        }
        endpoint, timeout, who = ANSWERS_URL, 30, "Brave Answers"

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException:
        return jsonify(error=f"Could not reach {who}."), 502

    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200:
        # Upstream error bodies may carry internals; relay only the status.
        return jsonify(error=f"{who} error (HTTP {resp.status_code})."), 502

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return jsonify(error=f"Unexpected response from {who}."), 502
    text = msg.get("content") or ""
    citations = data.get("citations") or msg.get("citations") or []
    if isinstance(citations, list):
        citations = [str(c) for c in citations if c]
    payload = {"answer": text, "citations": citations}
    cache_set(ckey, payload, hours)
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
