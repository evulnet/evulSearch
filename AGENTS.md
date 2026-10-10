# AGENTS.md

## Project mode
REAL — GitHub-hosted, tested, documented. Push target: **private** repo `nkavassalis/evulSearch` (verify `gh auth status` shows `nkavassalis` before pushing). All unit + Playwright tests must pass before any push. Local `.git/hooks/pre-push` guard from the SIMPLE era was removed on promotion; do not reintroduce remotes other than `origin`.

## Purpose
evulSearch: a small self-hosted, privacy-focused search service. A Flask server proxies Brave Search APIs (and optionally Bright Data SERP for Google/Bing) so API keys never reach the browser. Intended for private networks only (e.g. served over WireGuard); the app itself must never be exposed publicly. UI state lives in browser localStorage; the server keeps no state or logs.

## Layout
- `app.py` — Flask server. Routes:
  - `GET /` → static SPA
  - `GET /api/search?q=&tab=web|news|images|places&count=&safesearch=&freshness=&spellcheck=&lat=&lon=&loc=` → sanitized results (cache hit adds `"cached": true`)
  - `POST /api/answer {q, freshness}` → `{answer, citations}` (Brave Answers, OpenAI-compatible endpoint; cache hit adds `"cached": true`)
  - `GET /api/config` → `{answers_enabled}` capability flag (no secrets)
  - Query/result cache: SQLite at `cache_db`, lazy purge on access, TTL stored
    per row. `cache_hours` (default 168) covers web/images/places/answers;
    `cache_hours_news` (default `-1`) covers news separately — do not default
    news to cached, news must not serve stale. Semantics: `-1` off, `0` forever,
    `>0` hours; negative ≠ -1 clamps to -1. Cache is trimmed (`.strip()`) query
    text, so padding never splits entries. Pre-TTL table schema is auto-dropped
    (disposable data). Config `config.json` (gitignored; template
    `config.example.json`); `EVUL_CONFIG_PATH` env overrides the path (tests use
    it for hermeticity — always pass a tmp config + absolute tmp db).
- `static/` — vanilla JS SPA (`index.html`, `app.js`, `style.css`). No frameworks, no external assets. Settings autosave flashes the changed row green (`.flash` animation).
- `tests/test_app.py` — unit tests, upstream monkeypatched.
- `tests/test_ui.py` + `tests/conftest.py` — Playwright UI tests: mock Brave (Flask, threads, free ports) + real `app.py` subprocess; mock mirrors real Brave quirks (images safesearch enum, error envelopes).
- `README.md` (human setup), `LICENSE` (MIT), `.env.example`, `requirements.txt`.

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill BRAVE_API_KEY (+ optional BRAVE_ANSWERS_KEY)
.venv/bin/python app.py                      # 127.0.0.1:5000
HOST=0.0.0.0 .venv/bin/python app.py         # private-network/LAN test bind
```

## Tests
```bash
.venv/bin/python -m pytest tests/ -q                        # unit
.venv/bin/python -m playwright install chromium             # once
.venv/bin/python -m pytest tests/test_ui.py -q              # browser UI
```

## Design decisions & constraints (do not undo without asking)
- **Query cache is intentional** (user request, supersedes earlier “no server state”):
  queries + sanitized results persist in `cache_db` up to `cache_hours`; README
  privacy section documents this honestly. Do not describe the server as
  “no logs”. `-1` disables everything (no file created at all).
- **Keys server-side only.** Brave keys come from env/`.env` (gitignored). Errors relay only Brave's error code / HTTP status, never upstream bodies.
- **Tabs never auto-query.** Tab click clears results and waits for Go/Enter (Enter inside the Places location box DOES run a Places search). Deep links + popstate DO auto-run (they represent already-run searches). Tab bar lives above the search form and is always visible on load; `buildTabs()` hides disabled tabs; answers hides when `/api/config` says the key is missing.
- **Brave hard limits (verified live):** count ≤ 20; offset ≤ 9 and offset+count ≤ 20 ⇒ no real pagination — do not add a "next page".
- **Images safe search:** endpoint enum is off|strict only; server maps moderate→strict for images. Do not send `moderate` there (422).
- **Answers:** separate key `BRAVE_ANSWERS_KEY`; endpoint `POST {BRAVE_BASE}/chat/completions` (OpenAI-compatible, non-streaming). `model` param is pinned to `brave-pro` by the plan — no model picker. Feature is fully optional: when the key is unset the tab AND its settings section hide via `/api/config`.
- **Places:** payload carries title/url/address/phone/coordinates/categories/
  opening/description/img. `img` extraction accepts `pictures` as live-Brave dict
  `{"results":[{"src"…}]}` OR list-of-dicts, falling back to `thumbnail.src` —
  real Brave returned the dict shape and a naive `[0]` index crashed with
  KeyError 0; keep the shape-tolerant extractor (unit-tested). UI: photo card →
  lightbox; everything else in a `<details>` popover; a result WITHOUT `url`
  must render its title as `<span class="place-name">`, never `href="#"` (that
  opened duplicate app tabs). Location priority (client-side): `#loc` tab override > geolocation (`places_location`, opt-in, session-memory only) > `places_preset` settings text (disabled in the UI while geolocation is on; also used as fallback if geolocation is denied) > none. Server composes `"{q} in {loc}"` for any `loc` (≤200 chars, whitespace-collapsed) and separately forwards validated `lat`/`lon` (±90/±180). `places_preset` is persisted in localStorage settings (it is a UI pref, like the rest); geolocation never is. The `#loc` input shows only when the Places tab is active and is deliberately NOT persisted.
- **Answers markdown:** rendered by a built-in safe subset renderer in app.js (`renderMarkdown`/`mdInlineInto`) that builds DOM nodes; never use `innerHTML` for answer text. Raw HTML in answers renders as literal text (asserted in UI tests).
- **Embedded map = opt-OUT by default (user decision 2026-10):** user cog
  `map_embed` defaults TRUE and server `allow_map_embed` defaults TRUE; either
  side off disables (server off also HIDES the cog row via
  `.map-embed-only`). When active with a Places search: Leaflet pinned 1.9.4
  from unpkg + OSM tiles. Clicking a place title calls `focusPin(resultIdx)`
  (flyTo/setView + openPopup + `.pin-pop` pulse class on the marker element);
  `pinMarkers` maps result index -> marker and is rebuilt in `maybeRenderMap`
  and cleared in `hideMap`. UI tests stub `window.L`
  (`add_init_script(FAKE_LEAFLET)`, counters for maps/markers/popups/flies) so
  no CDN is ever fetched in tests — never let tests hit real tile servers. The
  geolocation test context needs the stub too (it renders the map by default).
- **Lightbox:** vanilla JS, opens on grid-image click; loads
  `result.origin` (Brave `properties.url`, original res), `onerror` falls back
  to the 500px proxy thumb with a visible note + “original” link. Esc/←/→
  handled in the shared keydown handler (guard on `!lightboxEl.hidden`).
- **Rich search intentionally absent:** needs a `callback_key` from a `rich` mixed-result entry; this subscription never emits one.
- **Places titles never navigate** (user decision 2026-10): every title is
  `<span class="place-title" role="button">` and only calls `focusPin(i)`;
  the website is reachable solely via the popover Website button. Blurb under
  the title = `description || categories.split(",")[0]`. UI scale setting
  `ui_scale` (75/100/125/150, default 100) applies CSS `zoom` on `<html>` via
  `applyZoom()` — keep it clamped to the whitelist; `for key of DEFAULTS`
  wiring auto-persists any setting whose control id matches the key.
- **Engine modularity (user decision 2026-10):** search backends are a
  capability registry, not conditionals. `app.py`: `BRAVE_CAPS` / `BD_CAPS`
  map engine id -> tabs it serves; `engines_available()` builds `/api.config
  engines: [{id, tabs}]` from present keys (BD needs BOTH key+zone); the only
  provider outside Brave is `_brightdata_serp()` (+`_bd_map()`), selected by
  `engine` param with per-engine URL builders. To add an engine: add caps, a
  builder branch, and a label in app.js `ENGINE_LABELS`. Frontend derives
  everything from `/api/config`: pills via `enginesFor(tab)` (shown when >1),
  requests use `engineForRequest(tab)` (falls back to brave when the saved
  engine can't serve the tab), tabs hide when no engine lists them. Gotchas:
  BrightData needs the generic `settings` loops in app.js to null-check
  controls (engine has no input — `for key of DEFAULTS` skips missing ids);
  BD payload requires `q=` FIRST in the target URL; `parsed_light` is
  web-organic-only, news needs `data_format:"parsed"`; only Google/Bing are
  parsed by BD (DDG/Yahoo/etc. would return raw HTML — do not add them without
  a parsing plan). LIVE-VERIFIED facts (live zone, 2026-10): `num=10` is
  rejected by BD (empty body; only send `&num=20`, never other values); first
  cold scrape can exceed 30s (timeout 90); Google Places = `tbm=lcl` full JSON
  `snack_pack` (name/rating/reviews_cnt/type/address/cid; images are base64
  megabytes — strip; no coordinates so no pins; `cid` -> maps link; loc
  override rides in the query text); Bing `display_link` can carry breadcrumb
  junk — `_bd_map` falls back to netloc when it isn't host-like. Google Images
  = `tbm=isch` full JSON `images[]` (only `original_image` is a usable URL;
  `link` is a google goto redirect; thumbs are base64-only -> originals double
  as thumbs). `tbm=lcl`/`tbm=isch` return EMPTY under parsed_light — full
  parse only; lcl carries ~15MB-equivalent base64 place photos (no documented
  exclusion flag) -> ~60-90s scrapes, BD timeout 120s, mitigated by the query
  cache on repeats.
- **Kagi provider (user addition 2026-10; first native-index option besides
  Brave).** Env `KAGI_KEY` (user's chosen name), `KAGI_SEARCH_URL` overrides
  for tests. POST-only JSON API; result type is the BODY FIELD `workflow`
  (search|news|images) — the `tbm=`/`tab=` operator guesses are decoys that
  silently return web results. Docs page is a JS shell; the real spec
  downloads from /api/docs/_bundle/openapi.json. Auth `Bearer {key}` per spec
  (`Bot` prefix also works but follow the spec). Buckets are singular
  (`data.search|news|image`). Snippets carry HTML -> `_strip_tags`; times are
  ISO -> `_rel_age` (unit-covered). Images: `image.url` full +
  `props.thumbnail.url` proxy. Freshness: undocumented-but-live
  `operators:[["tbs","qdr:x"]]` web/news only; safe via `!safe=` Teletype
  token appended to the query. Caps = web/news/images (NO places; no local
  pack product). PAY-AS-YOU-GO, no free tier — README says so; keep that
  sentence true if the matrix changes. UI engine test includes the Kagi pill:
  clicking pills persists in the context, so the news-step restores Google
  before the Places step.
- **SearchApi.io provider (`SEARCHAPI_KEY` — NOT `DUCKDUCKGO_KEY`, user
  corrected the naming twice: it is a commercial SERP vendor, zero-retention
  is enterprise-only, do NOT brand it privacy-forward; there is no official
  public DuckDuckGo API).** Adds engine `duckduckgo` (caps web/images ONLY —
  `duckduckgo_news`/`duckduckgo_maps` engines return "Unsupported engine" on
  the free plan, verified live; DDG freshness rides `df=d|w|m|y`, safe via
  `safe=off|moderate|strict`) and upgrades Bing to web/news/images (news =
  `bing_news` -> organic_results with date/source; images = `bing_images` ->
  `images[]`). Google caps stay full. LIVE shapes (2026-10): web/news
  `organic_results`; images `images[]` with NESTED `original{link}`/
  `thumbnail`/`source{name,link}` (mapper `_f()` tolerates str-or-dict);
  places = plain `engine=google` -> `local_results[]` LIST (not the
  serpapi-style dict!) with `gps_coordinates` but NO website link -> url must
  map to "" (frontend renders plain span — keep). Default orders: google
  [serpapi, searchapi, brightdata] (serpapi richer: website/phone/hours),
  bing [searchapi, brightdata], duckduckgo [searchapi]. Unit tests must pin
  SEARCHAPI_KEY/URL like every provider (dev .env has a real key — unpinned
  tests hit the real vendor and burn the 100/mo quota).
- **Admin provider levers (config.json, user decision 2026-10):**
  `engine_priority` = per-engine provider order (omitted provider = disabled
  for that engine; `[]` disables the engine and hides its pill) and
  `provider_quotas` = `{provider: {per_day, per_month}}` UTC-reset budgets
  counted in the `provider_usage` table of cache_db (counts only, never query
  text; sqlite reuse is deliberate — no new file sprawl). Quota exhaustion
  FALLS THROUGH to the next covering provider (rich-but-metered degrades to
  cheap, never errors if a fallback exists; all-capped -> 429 naming quotas).
  Cache hits never bump usage (placement of usage_bump after cache_get is
  load-bearing). engines_available() reflects priority/disable state but NOT
  live quota (pills would flicker mid-day). Cleaners `_clean_priority`/
  `_clean_quotas` harden config.json; missing BRAVE key is now per-engine 503
  (old global 500 guard removed — providers can be brave-less legitimately).
- **SerpApi shapes (LIVE-VERIFIED 2026-10, free plan):** web `organic_results`,
  news `news_results` (fallback `organic_results`), images `images_results`
  (`original` = direct URL, `thumbnail` = gstatic proxy), places = engine
  google + `fetch_place_results=true` -> `local_results.places[]` with
  `gps_coordinates` + `links{website,phone}` + `hours` + `thumbnail_large`
  (SerpApi's own `thumbnail` URL expires — use thumbnail_large). The
  `google_locations` ENGINE is NOT on this plan (400 Unsupported) — do not
  switch back. BrightData lcl has NO coordinates (verified) — SerpApi is the
  only Google-provider route to map pins; frontend map keys off coordinates
  presence, provider-agnostic by design.
- **SearXNG LIVE on the LAN** (URL lives in `.env` `SEARXNG_URL` only; instance has json format enabled — never write the IP into tracked files);
  images entries often have empty `thumbnail` -> mapper falls back to img_src.
- **Answers = multi-backend (user decision 2026-10):** `/api/answer` takes
  `engine: brave|local`; both speak OpenAI chat-completions so the parse
  (choices[0].message.content + citations) and the safe-markdown renderer are
  shared. Local backend = env `LOCAL_CHAT_BASE_URL` (+ optional key/model/
  label); model auto-detects via GET {base}/models first-id, pinned by
  `LOCAL_CHAT_MODEL`, cached per process in `_local_model_cache`. Pill label =
  `LOCAL_CHAT_LABEL` (admin-level rename; per-user rename deliberately not
  built — endpoint is the admin's choice). Config exposes
  `answers_backends: [{id,label}]`; frontend pills reuse `#engines` row with
  `settings.answers_engine`. Upstream error bodies are NOT relayed (status
  only) — see test_answer_upstream_error_status_only. http:// plaintext
  allowed for LAN: documented caution in README Privacy notes.
- **CSS `[hidden]` pitfall:** any rule setting `display:` on an element beats
  the UA `[hidden]{display:none}` — `#tabs`, `.row` and `.overlay` each need an
  explicit `[hidden] { display: none }` counterpart. Add one whenever giving a
  hideable element a display rule.
- **Desktop split view (Places):** `<main>.places-mode` + `#workspace` CSS grid
  (map left sticky, listings right) at ≥900px; JS only toggles the class via
  `updateLayout()` (called from `selectTabVisually`, `hideMap`, and map render).
  After layout toggles call `mapObj.invalidateSize()` guarded by identity check
  `mapObj === m` inside rAF — removed Leaflet maps throw on internals
  (`_leaflet_pos`), and the stubbed map lacks the method; keep both guards.
- **XSS hygiene:** all upstream text rendered via `textContent`/`<template>` stripping; never `innerHTML` with Brave data. Outbound links `rel="noopener noreferrer"`.
- **Settings** (localStorage key `"settings"`, defaults in `DEFAULTS` in app.js): theme, safesearch, count, freshness, spellcheck(web), answers_freshness, enabled_answers/news/images/places, places_location. Pre-paint theme script in `index.html` prevents flash — keep it.
- `BRAVE_BASE` env override exists only so tests can point at the mock Brave; default is the real API.

## Conventions
- Single-file Flask app on purpose; keep it that way at this scale.
- Update CHANGELOG.md with every meaningful change (user requirement 2026-10).
- Mobile renders at ui_scale x 0.75 (<900px viewport, applyZoom); keep the
  multiplier when touching zoom. Favicon set: `static/favicon.svg` is the
  source of truth; PNGs are rasterizations (regenerate: render the SVG in a
  data-URI `<img>` under Playwright Chromium at 32/192/180 and screenshot).
  Safari needs the PNG/apple-touch-icon — do not delete them.
- Before any commit: scan `git diff --cached` for secrets/internal addresses; never commit `.env`.
- Docs (README/AGENTS) must stay abstract — no LAN IPs, hostnames, VPN topology, or key material ever.

## Known issues / TODOs
- Cache purge is lazy (on access); an idle server never deletes expired rows.
- No query caching; every search hits Brave.
- Answers markdown is rendered as plain text (deliberate, avoids HTML injection); a safe markdown renderer is a possible improvement.
- Deep-link on Back re-runs upstream queries (no local cache).

## Bug pass notes (2026-10, pre-public)
- BD engines: explicit `safe=off`/`adlt=off` when user disables safe search
  (omitting it silently kept Google filtered); Google `tbs` freshness now only
  sent for web/news (never places/images).
- `engineForRequest` falls back to the FIRST engine that serves the tab (was a
  hardcoded "brave" -> guaranteed 500 on a BrightData-only server).
- Model-list parsing hardened (non-dict payloads / odd entries can't 500 it).
- Engine deep links are real now: `&engine=` is written into the URL and read
  on load/popstate (runtime-only, never persisted). Initial deep-link run moved
  inside the /api/config callback so capability validation sees real caps.
- `enginesFor` tolerates entries without `tabs`.
- Unit tests must hermetically pin module globals (LOCAL_CHAT_URL etc.) — the
  dev .env is loaded by app.py on import and used to leak into assertions.

## Testing limitations / unverified
- Web `spellcheck=false` param is forwarded per Brave docs, and `altered_query` (if present) is surfaced — but Brave never returned an altered query for typo probes on this plan, so the "showing results for" notice is unverified end-to-end with the real API.
- Answers real citations list was empty on the live probe; citation parsing follows the documented shape and is unit-tested against a mock only.
- Places geolocation verified with Playwright's geolocation override (Chromium), not a real mobile browser prompt.
- Flask dev server: adequate for private-network use; not hardened for anything public.

## Guardrails in effect

- The GitHub repo is **PUBLIC** (nkavassalis/evulSearch, since 2026-10, launched
  from a single squashed initial commit — keep history clean; never force-push
  secrets or internal paths, and treat every commit as world-readable forever).
- The *application* must still never be publicly exposed (Flask dev server,
  no auth): private networks only.
- Never commit `.env`, `config.json`, `*.db`, real IPs/hostnames/zone names,
  or anything matching the secret-scan patterns in AGENTS. Docs describe
  setup abstractly (`DATABASE_URL`-style placeholders only).
- Repo **public**; account `nkavassalis`; single-maintainer flow: direct push to `main` after full green suite (no branch-and-PR).
- Bright Data SERP path is LIVE-VERIFIED end-to-end (configured zone):
  Google web/news/places and Bing web all returned real results through the
  proxy. SerpApi (key) and SearXNG (LAN instance) are LIVE-VERIFIED end to end
  (web/images/places; shapes recorded above). Bright Data Places carry NO
  coordinates (verified live) — SerpApi/Brave enable the map, BrightData
  cannot; the frontend map is provider-agnostic (renders on coordinates
  presence). Safe-search `off`/`strict` flags for Google/Bing are unit-verified
  only (URL construction). Not verified: Bing freshness (unmapped by design),
  Bing market/locale defaults (returned US-flavored results from this host).
