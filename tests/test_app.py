import json
import sqlite3

import pytest

import app as appmod


ANSWER_OK = {
    "choices": [{"message": {"content": "**K2**"}}],
    "citations": ["https://a.example", "https://b.example"],
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(appmod, "API_KEY", "test-key")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "type": "search",
            "web": {"results": [
                {"title": "T", "url": "https://e.com", "description": "<b>D</b>",
                 "age": "1 day ago", "extra": "should-be-stripped"},
            ]}
        }

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def patch_get(monkeypatch, resp):
    calls = {"urls": []}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["url"] = url
        calls["params"] = params
        calls["headers"] = headers
        calls["urls"].append(url)
        return resp

    monkeypatch.setattr(appmod.requests, "get", fake_get)
    return calls


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Default: cache disabled + temp db, so tests never share state.
    Provider keys pinned off hermetically: the developer's .env must not
    reorder providers (dotenv loads it into os.environ on import)."""
    monkeypatch.setattr(appmod, "CONFIG", {
        "cache_hours": -1, "cache_hours_news": -1,
        "cache_db": str(tmp_path / "cache.db"),
    })
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "")
    monkeypatch.setattr(appmod, "SEARCHAPI_KEY", "")
    monkeypatch.setattr(appmod, "SEARCHAPI_URL", "http://sa.test/api/v1/search")
    monkeypatch.setattr(appmod, "KAGI_KEY", "")
    monkeypatch.setattr(appmod, "KAGI_URL", "http://kagi.test/api/v1/search")
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")


def _enable_cache(tmp_path, monkeypatch, hours, news_hours=-1):
    monkeypatch.setattr(appmod, "CONFIG", {
        "cache_hours": hours, "cache_hours_news": news_hours,
        "cache_db": str(tmp_path / "cache.db"),
    })


def test_cache_hit_skips_upstream(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, 168)
    calls = patch_get(monkeypatch, FakeResp())
    first = client.get("/api/search?q=cached").get_json()
    second = client.get("/api/search?q=cached").get_json()
    assert "cached" not in first
    assert second["cached"] is True
    assert second["results"] == first["results"]
    assert len(calls["urls"]) == 1  # upstream hit exactly once


def test_cache_expiry_and_purge(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, 1)  # 1 hour
    patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=old")
    # age the entry beyond the TTL, next request must miss AND purge it
    conn = sqlite3.connect(str(tmp_path / "cache.db"))
    conn.execute("UPDATE cache SET created = created - 7200")
    conn.commit()
    conn.close()
    r = client.get("/api/search?q=old")
    assert "cached" not in r.get_json()
    conn = sqlite3.connect(str(tmp_path / "cache.db"))
    assert conn.execute("SELECT COUNT(*) FROM cache WHERE created < "
                        "(strftime('%s','now') - 3600)").fetchone()[0] == 0
    conn.close()


def test_cache_indefinite(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, 0)  # never expires
    patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=forever")
    conn = sqlite3.connect(str(tmp_path / "cache.db"))
    conn.execute("UPDATE cache SET created = 0")
    conn.commit()
    conn.close()
    assert client.get("/api/search?q=forever").get_json().get("cached") is True


def test_cache_disabled(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, -1, news_hours=-1)
    calls = patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=uncached")
    r = client.get("/api/search?q=uncached")
    assert "cached" not in r.get_json()
    assert len(calls["urls"]) == 2
    assert not (tmp_path / "cache.db").exists()


def test_cache_news_separate_default_off(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, 168, news_hours=-1)
    calls = patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=breaking&tab=news")
    r = client.get("/api/search?q=breaking&tab=news").get_json()
    assert "cached" not in r          # news default: never cached
    assert len(calls["urls"]) == 2
    # ...while web still caches under the general setting
    client.get("/api/search?q=breaking&tab=web")
    assert client.get("/api/search?q=breaking&tab=web").get_json().get("cached") is True


def test_cache_news_enabled(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, 168, news_hours=6)
    # non-empty payload: empties are deliberately never cached now
    patch_get(monkeypatch, FakeResp(payload={"results": [
        {"title": "H", "url": "https://e.com/1", "description": "d",
         "age": "1h", "page_age": "2026-10-10"}]}))
    client.get("/api/search?q=fresh&tab=news")
    assert client.get("/api/search?q=fresh&tab=news").get_json().get("cached") is True


def test_cache_key_strips_surrounding_whitespace(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, 168)
    calls = patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=%20%20spaced%20%20")
    assert client.get("/api/search?q=spaced").get_json().get("cached") is True
    assert len(calls["urls"]) == 1   # padded and bare query share one entry


def test_load_config_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"cache_db": "/tmp/x.db"}')
    monkeypatch.setattr(appmod, "CONFIG_PATH", str(cfg))
    c = appmod.load_config()
    assert c["cache_hours"] == 168        # one week
    assert c["cache_hours_news"] == -1    # news uncached by default
    assert c["allow_map_embed"] is True   # map served by default (opt-out model)
    # garbage values fall back to defaults; any negative != -1 clamps to -1
    cfg.write_text('{"cache_hours": "nonsense", "cache_hours_news": -5, "allow_map_embed": false}')
    c = appmod.load_config()
    assert c["cache_hours"] == 168 and c["cache_hours_news"] == -1
    assert c["allow_map_embed"] is False  # explicit opt-out honored


def test_answer_cached(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, 168)
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "akey")
    calls = patch_post(monkeypatch, ANSWER_OK)
    client.post("/api/answer", json={"q": "k2"})
    second = client.post("/api/answer", json={"q": "k2"}).get_json()
    assert second["cached"] is True
    assert calls["count"] == 1


def test_search_returns_sanitized_results(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp())
    r = client.get("/api/search?q=hello")
    assert r.status_code == 200
    body = r.get_json()
    assert body["results"] == [
        {"title": "T", "url": "https://e.com", "description": "<b>D</b>", "age": "1 day ago"}
    ]
    assert calls["headers"]["X-Subscription-Token"] == "test-key"
    assert calls["params"] == {"q": "hello", "safesearch": "moderate", "count": 10, "spellcheck": "true"}


def test_search_accepts_legacy_nested_shape(client, monkeypatch):
    patch_get(monkeypatch, FakeResp(payload={
        "results": {"web": {"results": [
            {"title": "Old", "url": "https://old.com", "description": "d", "age": ""},
        ]}}}))
    body = client.get("/api/search?q=x").get_json()
    assert body["results"] == [
        {"title": "Old", "url": "https://old.com", "description": "d", "age": ""}
    ]


def test_invalid_params_are_coerced(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp())
    r = client.get("/api/search?q=hi&safesearch=zzz&count=999")
    assert r.status_code == 200
    assert calls["params"]["safesearch"] == "moderate"
    assert calls["params"]["count"] == 10


def test_freshness_mapped(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=x&freshness=week")
    assert calls["params"]["freshness"] == "pw"


def test_freshness_invalid_or_any_omitted(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=x&freshness=bogus")
    assert "freshness" not in calls["params"]


def test_news_tab(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp(payload={
        "results": [
            {"title": "N", "url": "https://n.com", "description": "d", "age": "2h ago"},
        ]
    }))
    body = client.get("/api/search?q=x&tab=news").get_json()
    assert body["tab"] == "news"
    assert body["results"][0]["title"] == "N"
    assert calls["url"].endswith("/news/search")


def test_images_tab(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp(payload={
        "results": [
            {"title": "I", "url": "https://i.com", "source": "i.com",
             "properties": {"url": "https://orig.example/full.jpg"},
             "thumbnail": {"src": "https://cdn/t.jpg", "width": 500, "height": 500}},
            {"title": "no thumb", "url": "https://x.com"},
        ]
    }))
    body = client.get("/api/search?q=x&tab=images").get_json()
    assert body["tab"] == "images"
    assert body["results"] == [
        {"title": "I", "url": "https://i.com", "source": "i.com",
         "thumb": "https://cdn/t.jpg", "origin": "https://orig.example/full.jpg"}
    ]
    assert calls["url"].endswith("/images/search")
    assert calls["params"]["safesearch"] == "strict"  # moderate coerced to strict for images


def test_images_safesearch_off(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp(payload={"results": []}))
    client.get("/api/search?q=x&tab=images&safesearch=off")
    assert calls["params"]["safesearch"] == "off"


def test_invalid_tab_defaults_to_web(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp())
    body = client.get("/api/search?q=x&tab=bogus").get_json()
    assert body["tab"] == "web"
    assert calls["url"].endswith("/web/search")


def test_config_endpoint(client, monkeypatch):
    # Dev .env may define LOCAL_CHAT_BASE_URL; keep this test hermetic.
    monkeypatch.setattr(appmod, "LOCAL_CHAT_URL", "")
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "akey")
    monkeypatch.setitem(appmod.CONFIG, "allow_map_embed", False)
    monkeypatch.setattr(appmod, "API_KEY", "k")
    monkeypatch.setattr(appmod, "BD_KEY", "")
    body = client.get("/api/config").get_json()
    assert body["answers_enabled"] is True and body["map_embed_allowed"] is False
    assert body["engines"] == [{"id": "brave", "tabs": appmod.BRAVE_CAPS}]
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "")
    monkeypatch.setitem(appmod.CONFIG, "allow_map_embed", True)
    body = client.get("/api/config").get_json()
    assert body["answers_enabled"] is False and body["map_embed_allowed"] is True


def test_config_engines_reflect_keys(client, monkeypatch):
    monkeypatch.setattr(appmod, "API_KEY", "k")
    monkeypatch.setattr(appmod, "BD_KEY", "bk")
    monkeypatch.setattr(appmod, "BD_ZONE", "bz")
    ids = {e["id"]: e["tabs"] for e in client.get("/api/config").get_json()["engines"]}
    assert set(ids) == {"brave", "google", "bing"}
    assert "news" in ids["google"] and "news" not in ids["bing"]
    assert ids["brave"] == appmod.BRAVE_CAPS
    # zone missing -> BD engines not offered at all
    monkeypatch.setattr(appmod, "BD_ZONE", "")
    ids = {e["id"] for e in client.get("/api/config").get_json()["engines"]}
    assert ids == {"brave"}


def _patch_post(monkeypatch, payload):
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        return FakeResp(payload=payload)

    monkeypatch.setattr(appmod.requests, "post", fake_post)
    return calls


def _bd_env(monkeypatch):
    monkeypatch.setattr(appmod, "BD_KEY", "bk")
    monkeypatch.setattr(appmod, "BD_ZONE", "bz")


def test_google_web_via_brightdata(client, monkeypatch):
    _bd_env(monkeypatch)
    calls = _patch_post(monkeypatch, {"organic": [
        {"title": "Google &amp; results", "link": "https://g.example/1",
         "description": "d1", "display_link": "g.example"},
        {"title": "no link skipped", "description": "x"},
    ]})
    body = client.get("/api/search?q=pizza+NYC&engine=google").get_json()
    assert body["engine"] == "google" and body["tab"] == "web"
    assert body["results"][0]["title"] == "Google & results"  # entities unescaped
    assert body["results"][0]["source"] == "g.example"
    assert len(body["results"]) == 1
    assert calls["url"] == "https://api.brightdata.com/request"
    assert calls["headers"]["Authorization"] == "Bearer bk"
    assert calls["json"]["zone"] == "bz"
    # q must be the first query arg (BrightData requirement)
    assert calls["json"]["url"].startswith("https://www.google.com/search?q=pizza+NYC")
    assert calls["json"]["data_format"] == "parsed_light"


def test_google_news_via_brightdata(client, monkeypatch):
    _bd_env(monkeypatch)
    calls = _patch_post(monkeypatch, {"news": [
        {"title": "N1", "link": "https://n.example/1", "description": "x",
         "source": "n.example", "time": "2h ago"},
    ]})
    body = client.get("/api/search?q=x&engine=google&tab=news&freshness=week").get_json()
    assert body["tab"] == "news" and body["engine"] == "google"
    assert body["results"][0]["age"] == "2h ago"
    assert "tbm=nws" in calls["json"]["url"]
    assert "tbs=qdr%3Aw" in calls["json"]["url"] or "tbs=qdr:w" in calls["json"]["url"]
    assert calls["json"]["data_format"] == "parsed"  # news needs full JSON


def test_engine_tab_guards(client, monkeypatch):
    _bd_env(monkeypatch)
    r = client.get("/api/search?q=x&engine=bing&tab=news")
    assert r.status_code == 400  # bing parsed = web only
    monkeypatch.setattr(appmod, "BD_KEY", "")
    r = client.get("/api/search?q=x&engine=google")
    assert r.status_code == 503  # not configured -> refuse, don't silently switch
    # unknown engine falls back to brave, not an error
    monkeypatch.setattr(appmod, "API_KEY", "k")
    calls = patch_get(monkeypatch, FakeResp())
    r = client.get("/api/search?q=x&engine=askJeeves")
    assert r.status_code == 200 and "engine" not in r.get_json()


def test_places_tab(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp(payload={
        "results": [
            {"title": "P", "url": "https://p.com", "junk": 1,
             "postal_address": {"displayAddress": "1 Main St"},
             "contact": {"telephone": "123"},
             "coordinates": [1.0, 2.0], "categories": ["pizza", "food"],
             "description": "Cafe",
             "opening_hours": {"current_day": [
                 {"full_name": "Saturday", "opens": "09:00", "closes": "18:00"}]},
             "pictures": {"results": [{"src": "https://cdn/p1.jpg"}]},
             "thumbnail": {"src": "https://cdn/thumb.jpg"}},
        ]
    }))
    body = client.get("/api/search?q=x&tab=places").get_json()
    assert body["tab"] == "places"
    assert body["results"] == [{
        "title": "P", "url": "https://p.com", "address": "1 Main St",
        "phone": "123", "coordinates": [1.0, 2.0], "categories": "pizza, food",
        "opening": "Saturday 09:00-18:00", "description": "Cafe",
        "img": "https://cdn/p1.jpg",
    }]
    assert calls["url"].endswith("/local/place_search")


def test_spellcheck_default_and_off(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp())
    client.get("/api/search?q=x")
    assert calls["params"]["spellcheck"] == "true"
    client.get("/api/search?q=x&spellcheck=false")
    assert calls["params"]["spellcheck"] == "false"
    # spellcheck param only on web tab
    client.get("/api/search?q=x&tab=news")
    assert "spellcheck" not in calls["params"]


def test_altered_query_surfaced(client, monkeypatch):
    patch_get(monkeypatch, FakeResp(payload={
        "web": {"altered_query": "piece of cake", "results":
                [{"title": "T", "url": "u", "description": "d", "age": ""}]}
    }))
    body = client.get("/api/search?q=pice+of+cake").get_json()
    assert body["altered_query"] == "piece of cake"


def test_altered_query_omitted_when_same(client, monkeypatch):
    patch_get(monkeypatch, FakeResp(payload={
        "web": {"altered_query": "hello", "results": []}
    }))
    body = client.get("/api/search?q=hello").get_json()
    assert "altered_query" not in body


def test_missing_query(client):
    assert client.get("/api/search").status_code == 400
    assert client.get("/api/search?q=%20%20").status_code == 400


def test_query_too_long(client):
    r = client.get("/api/search?q=" + "x" * 401)
    assert r.status_code == 400


def test_brave_error_is_not_relayed(client, monkeypatch):
    class Boom(FakeResp):
        def json(self):
            return {"error": "internal detail"}

    monkeypatch.setattr(appmod.requests, "get",
                        lambda *a, **k: Boom(status_code=500))
    r = client.get("/api/search?q=x")
    assert r.status_code == 502
    assert "500" in r.get_json()["error"]
    assert "internal detail" not in json.dumps(r.get_json())


def test_brave_network_failure(client, monkeypatch):
    def boom(*a, **k):
        raise appmod.requests.RequestException("down")

    monkeypatch.setattr(appmod.requests, "get", boom)
    assert client.get("/api/search?q=x").status_code == 502


def test_missing_api_key(client, monkeypatch):
    monkeypatch.setattr(appmod, "API_KEY", "")
    assert client.get("/api/search?q=x").status_code == 503


def patch_post(monkeypatch, resp):
    calls = {"count": 0}

    class R:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._p = payload if payload is not None else ANSWER_OK

        def json(self):
            return self._p

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["count"] += 1
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        return R(payload=resp if isinstance(resp, dict) else None) \
            if isinstance(resp, dict) else R()

    monkeypatch.setattr(appmod.requests, "post", fake_post)
    return calls


def test_places_coords_passed_and_validated(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp(payload={"results": []}))
    client.get("/api/search?q=coffee&tab=places&lat=37.98&lon=23.72")
    assert calls["params"]["latitude"] == 37.98
    assert calls["params"]["longitude"] == 23.72
    # out-of-range or junk coords are dropped, request still succeeds
    client.get("/api/search?q=coffee&tab=places&lat=999&lon=0")
    assert "latitude" not in calls["params"]
    client.get("/api/search?q=coffee&tab=places&lat=nonsense")
    assert "latitude" not in calls["params"]


def test_answer_freshness_mapped(client, monkeypatch):
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "akey")
    calls = patch_post(monkeypatch, ANSWER_OK)
    client.post("/api/answer", json={"q": "x", "freshness": "week"})
    assert calls["json"]["freshness"] == "pw"


def test_places_loc_override(client, monkeypatch):
    calls = patch_get(monkeypatch, FakeResp(payload={"results": []}))
    client.get("/api/search?q=coffee&tab=places&loc=Tokyo,\n Japan")
    assert calls["params"]["q"] == "coffee in Tokyo, Japan"  # newlines collapsed
    client.get("/api/search?q=coffee&tab=places&loc=")
    assert calls["params"]["q"] == "coffee"  # empty loc: untouched query
    assert "loc" not in calls["params"]


def test_answer_ok(client, monkeypatch):
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "akey")
    calls = patch_post(monkeypatch, ANSWER_OK)
    body = client.post("/api/answer", json={"q": "k2"}).get_json()
    assert body["answer"] == "**K2**"
    assert body["citations"] == ["https://a.example", "https://b.example"]
    assert calls["url"].endswith("/chat/completions")
    assert calls["headers"]["x-subscription-token"] == "akey"
    assert calls["json"]["messages"] == [{"role": "user", "content": "k2"}]


def test_answer_missing_key(client, monkeypatch):
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "")
    assert client.post("/api/answer", json={"q": "x"}).status_code == 503


def test_answer_empty_query(client, monkeypatch):
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "akey")
    patch_post(monkeypatch, None)
    assert client.post("/api/answer", json={"q": "  "}).status_code == 400


def test_answer_upstream_error_status_only(client, monkeypatch):
    # Upstream error bodies (which can carry internal codes/details) must NOT
    # be relayed to the browser — only the HTTP status is shown.
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "akey")

    class R:
        status_code = 500
        text = '{"error": {"code": "INTERNAL", "detail": "internal-blob"}}'
        def json(self):
            return {"error": {"code": "INTERNAL", "detail": "internal-blob"}}

    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: R())
    body = client.post("/api/answer", json={"q": "x"}).get_json()
    assert "500" in body["error"]
    assert "internal-blob" not in body["error"] and "RATE_LIMITED" not in body["error"]


def test_index_served(client):
    assert client.get("/").status_code == 200


def test_google_places_via_brightdata(client, monkeypatch):
    _bd_env(monkeypatch)
    calls = _patch_post(monkeypatch, {"snack_pack": [
        {"cid": "9", "name": "A ramen spot", "rating": 4.5, "reviews_cnt": 123,
         "type": "ramen", "address": "1 Tsukiji", "image": "data:image/jpeg;base64,BIG"},
        {"cid": "7"},  # no name -> skipped
    ]})
    body = client.get("/api/search?q=ramen&engine=google&tab=places&loc=Tokyo").get_json()
    assert body["tab"] == "places" and body["engine"] == "google"
    r0 = body["results"][0]
    assert r0["url"] == "https://www.google.com/maps?cid=9"
    assert r0["description"] == "4.5\u2605 (123 reviews)"
    assert r0["img"] == ""            # base64 blobs must never reach the client
    assert r0["coordinates"] == []    # no pins for BD places
    assert len(body["results"]) == 1
    # loc override rides inside the query string (q stays first arg)
    assert "ramen+in+Tokyo" in calls["json"]["url"]
    assert "tbm=lcl" in calls["json"]["url"]


def test_google_images_via_brightdata(client, monkeypatch):
    _bd_env(monkeypatch)
    calls = _patch_post(monkeypatch, {"images": [
        {"original_image": "https://x.example/a.jpg", "image_alt": "Alt A",
         "image": "data:image/jpeg;base64,HUGE", "link": "https://google.com/goto?url=J"},
        {"image_alt": "skipped, no original"},
    ]})
    body = client.get("/api/search?q=ramen&engine=google&tab=images").get_json()
    assert body["tab"] == "images" and len(body["results"]) == 1
    r0 = body["results"][0]
    assert r0["thumb"] == "https://x.example/a.jpg"   # originals double as thumbs
    assert r0["title"] == "Alt A" and r0["source"] == "x.example"
    assert "base64" not in json.dumps(body)           # blobs never reach client
    assert "tbm=isch" in calls["json"]["url"]


def test_answer_local_autodetects_model(client, monkeypatch):
    monkeypatch.setattr(appmod, "LOCAL_CHAT_URL", "http://llm.test/v1")
    monkeypatch.setattr(appmod, "LOCAL_CHAT_MODEL", "")
    appmod._local_model_cache["model"] = ""
    gets = patch_get(monkeypatch, FakeResp(payload={"data": [{"id": "m1"}]}))
    posts = _patch_post(monkeypatch, {"choices": [{"message": {"content": "hi there"}}]})
    body = client.post("/api/answer", json={"q": "x", "engine": "local"}).get_json()
    assert body["answer"] == "hi there"
    assert gets["url"] == "http://llm.test/v1/models"       # auto-detect
    assert posts["json"]["model"] == "m1"
    assert posts["url"] == "http://llm.test/v1/chat/completions"


def test_answer_local_pinned_model(client, monkeypatch):
    monkeypatch.setattr(appmod, "LOCAL_CHAT_URL", "http://llm.test/v1")
    monkeypatch.setattr(appmod, "LOCAL_CHAT_MODEL", "pinned")
    posts = _patch_post(monkeypatch, {"choices": [{"message": {"content": "yo"}}]})
    gets = patch_get(monkeypatch, FakeResp(payload={"data": [{"id": "never"}]}))
    body = client.post("/api/answer", json={"q": "x", "engine": "local"}).get_json()
    assert body["answer"] == "yo" and posts["json"]["model"] == "pinned"
    assert "url" not in gets  # /models never called when pinned


def test_answer_local_unconfigured(client, monkeypatch):
    monkeypatch.setattr(appmod, "LOCAL_CHAT_URL", "")
    r = client.post("/api/answer", json={"q": "x", "engine": "local"})
    assert r.status_code == 503


def test_config_lists_answer_backends(client, monkeypatch):
    monkeypatch.setattr(appmod, "ANSWERS_KEY", "akey")
    monkeypatch.setattr(appmod, "LOCAL_CHAT_URL", "http://llm.test/v1")
    monkeypatch.setattr(appmod, "LOCAL_CHAT_LABEL", "vllm")
    b = client.get("/api/config").get_json()["answers_backends"]
    assert b == [{"id": "brave", "label": "Brave"}, {"id": "local", "label": "vllm"}]


def test_brightdata_safesearch_flags(client, monkeypatch):
    _bd_env(monkeypatch)
    calls = _patch_post(monkeypatch, {"organic": []})
    client.get("/api/search?q=x&engine=google&safesearch=off")
    assert "safe=off" in calls["json"]["url"]
    client.get("/api/search?q=x&engine=bing&safesearch=off")
    assert "adlt=off" in calls["json"]["url"]
    # freshness rides only on google web/news, never places/images
    _patch_post(monkeypatch, {"snack_pack": []})
    calls2 = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        calls2["url"] = json["url"]
        return FakeResp(payload={"snack_pack": []})
    monkeypatch.setattr(appmod.requests, "post", fake_post)
    client.get("/api/search?q=x&engine=google&tab=places&freshness=day")
    assert "tbs=" not in calls2["url"]


def test_favicon_served(client):
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200
    assert r.content_type.startswith("image/svg+xml")


def test_favicon_png_served(client):
    # Safari ignores SVG favicons; the PNG + apple-touch-icon are the real deal
    for path in ("/static/favicon-32.png", "/static/favicon-192.png",
                 "/static/apple-touch-icon.png"):
        r = client.get(path)
        assert r.status_code == 200 and r.content_type == "image/png", path


def _serp_env(monkeypatch):
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "sk")
    monkeypatch.setattr(appmod, "SERPAPI_URL", "https://sp.test/search.json")
    monkeypatch.setattr(appmod, "BD_KEY", "")
    monkeypatch.setattr(appmod, "BD_ZONE", "")


def test_serpapi_web_priority_and_mapping(client, monkeypatch):
    _serp_env(monkeypatch)
    monkeypatch.setattr(appmod, "BD_KEY", "bk")  # BD also configured...
    monkeypatch.setattr(appmod, "BD_ZONE", "bz")
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    calls = patch_get(monkeypatch, FakeResp(payload={"organic_results": [
        {"title": "S &amp; T", "link": "https://a.example/1",
         "snippet": "sn", "displayed_link": "a.example"},
    ]}))
    posts = _patch_post(monkeypatch, {"organic": []})  # BD must NOT be used
    body = client.get("/api/search?q=x&engine=google").get_json()
    assert body["results"][0]["title"] == "S & T"
    assert calls["url"] == "https://sp.test/search.json"   # SerpApi wins
    assert "url" not in posts                              # BrightData untouched
    assert body["results"][0]["source"] == "a.example"


def test_serpapi_places_coordinates_enable_map(client, monkeypatch):
    _serp_env(monkeypatch)
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    _patch_post(monkeypatch, {"organic": []})
    patch_get(monkeypatch, FakeResp(payload={"local_results": {"places": [
        {"title": "Osaka sushi", "address": "1 street",
         "links": {"website": "https://w.example"},
         "phone": "123", "rating": 4.4, "type": "sushi",
         "thumbnail_large": "https://img.example/1.jpg",
         "gps_coordinates": {"latitude": 34.7, "longitude": 135.5}},
    ]}}))
    body = client.get("/api/search?q=sushi&engine=google&tab=places").get_json()
    r0 = body["results"][0]
    assert r0["coordinates"] == [34.7, 135.5]  # map pins work like Brave
    assert r0["url"] == "https://w.example"
    assert r0["img"] == "https://img.example/1.jpg"


def test_searxng_mapping_and_guards(client, monkeypatch):
    monkeypatch.setattr(appmod, "SEARXNG_URL", "http://sx.test")
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "")
    monkeypatch.setattr(appmod, "BD_KEY", "")
    calls = patch_get(monkeypatch, FakeResp(payload={"results": [
        {"title": "SX &amp; T", "url": "https://s.example/1", "content": "c"},
    ]}))
    body = client.get("/api/search?q=x&engine=searxng").get_json()
    assert body["engine"] == "searxng"
    assert body["results"][0]["title"] == "SX & T"
    assert "format=json" in calls["url"] or calls["params"].get("format") == "json"
    # Places: SearXNG has none
    r = client.get("/api/search?q=x&engine=searxng&tab=places")
    assert r.status_code == 400


def test_searxng_json_disabled_hint(client, monkeypatch):
    monkeypatch.setattr(appmod, "SEARXNG_URL", "http://sx.test")
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "")
    monkeypatch.setattr(appmod, "BD_KEY", "")

    class R:
        status_code = 403
        def json(self):
            return {}

    monkeypatch.setattr(appmod.requests, "get", lambda *a, **k: R())
    body = client.get("/api/search?q=x&engine=searxng").get_json()
    assert "formats" in body["error"]  # actionable hint, not a bare 403


def test_config_engines_union_and_searxng(client, monkeypatch):
    monkeypatch.setattr(appmod, "API_KEY", "k")
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "sk")
    monkeypatch.setattr(appmod, "BD_KEY", "bk")
    monkeypatch.setattr(appmod, "BD_ZONE", "bz")
    monkeypatch.setattr(appmod, "SEARXNG_URL", "http://sx.test")
    engines = client.get("/api/config").get_json()["engines"]
    by_id = {e["id"]: e for e in engines}
    assert set(by_id) == {"brave", "google", "bing", "searxng"}
    # one Google pill for BOTH providers, tabs = union
    assert by_id["google"]["tabs"] == ["web", "news", "images", "places"]
    assert by_id["searxng"]["tabs"] == ["web", "news", "images"]


# --- admin levers: engine_priority + provider_quotas ---

def _both_google_providers(monkeypatch):
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "sk")
    monkeypatch.setattr(appmod, "SERPAPI_URL", "http://sp.test/search.json")
    monkeypatch.setattr(appmod, "BD_KEY", "bk")
    monkeypatch.setattr(appmod, "BD_ZONE", "bz")
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")


def test_priority_config_reverses_provider(client, monkeypatch):
    _both_google_providers(monkeypatch)
    monkeypatch.setitem(appmod.CONFIG, "engine_priority",
                        {"google": ["brightdata", "serpapi"]})
    gets = patch_get(monkeypatch, FakeResp(payload={"organic_results": []}))
    posts = _patch_post(monkeypatch, {"organic": []})
    client.get("/api/search?q=x&engine=google")
    assert "url" in posts and "url" not in gets  # BrightData first now


def test_priority_config_disables_engine(client, monkeypatch):
    _both_google_providers(monkeypatch)
    monkeypatch.setattr(appmod, "BD_KEY", "")  # only serpapi configured
    monkeypatch.setitem(appmod.CONFIG, "engine_priority", {"google": []})
    ids = {e["id"] for e in client.get("/api/config").get_json()["engines"]}
    assert "google" not in ids
    assert client.get("/api/search?q=x&engine=google").status_code == 503


def test_quota_falls_back_to_cheap_provider(client, monkeypatch, tmp_path):
    _both_google_providers(monkeypatch)
    monkeypatch.setitem(appmod.CONFIG, "cache_db", str(tmp_path / "usage.db"))
    monkeypatch.setitem(appmod.CONFIG, "provider_quotas",
                        {"serpapi": {"per_day": 1}})
    posts = _patch_post(monkeypatch, {"organic": []})
    gets = patch_get(monkeypatch, FakeResp(payload={"organic_results": []}))
    r1 = client.get("/api/search?q=a&engine=google")  # serpapi (1/day budget)
    r2 = client.get("/api/search?q=b&engine=google")  # quota hit -> BrightData
    assert r1.status_code == r2.status_code == 200
    assert "url" in gets and "url" in posts  # both providers used, in order


def test_quota_exhausted_single_provider_429(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "sk")
    monkeypatch.setattr(appmod, "SERPAPI_URL", "http://sp.test/search.json")
    monkeypatch.setattr(appmod, "BD_KEY", "")
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    monkeypatch.setattr(appmod, "API_KEY", "")
    monkeypatch.setitem(appmod.CONFIG, "cache_db", str(tmp_path / "usage.db"))
    monkeypatch.setitem(appmod.CONFIG, "provider_quotas",
                        {"serpapi": {"per_day": 1}})
    patch_get(monkeypatch, FakeResp(payload={"organic_results": []}))
    assert client.get("/api/search?q=a&engine=google").status_code == 200
    r = client.get("/api/search?q=b&engine=google")
    assert r.status_code == 429
    assert "daily quota" in r.get_json()["error"]


def test_serpapi_images_shape(client, monkeypatch):
    _serp_env(monkeypatch)
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    patch_get(monkeypatch, FakeResp(payload={"images_results": [
        {"title": "Fuji", "link": "https://wiki/fuji",
         "thumbnail": "https://gstatic/t.jpg", "original": "https://upload/fuji.jpg",
         "source": "Wikipedia"},
    ]}))
    body = client.get("/api/search?q=fuji&engine=google&tab=images").get_json()
    r0 = body["results"][0]
    assert r0["origin"] == "https://upload/fuji.jpg"   # direct image for lightbox
    assert r0["thumb"] == "https://gstatic/t.jpg"      # cheap grid thumbnail
    assert r0["url"] == "https://wiki/fuji"            # source page


# --- SearchApi provider (vendor; serves DDG/Google/Bing engines) ---

def _searchapi_env(monkeypatch, serpapi="", bd=False):
    monkeypatch.setattr(appmod, "SEARCHAPI_KEY", "sak")
    monkeypatch.setattr(appmod, "SEARCHAPI_URL", "http://sa.test/api/v1/search")
    monkeypatch.setattr(appmod, "SERPAPI_KEY", serpapi)
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    monkeypatch.setattr(appmod, "BD_KEY", "bk" if bd else "")
    monkeypatch.setattr(appmod, "BD_ZONE", "bz" if bd else "")


def test_searchapi_duckduckgo_web(client, monkeypatch):
    _searchapi_env(monkeypatch)
    calls = patch_get(monkeypatch, FakeResp(payload={"organic_results": [
        {"title": "DDG &amp; Results", "link": "https://d.example/1",
         "snippet": "sn"}]}))
    body = client.get("/api/search?q=x&engine=duckduckgo").get_json()
    assert body["engine"] == "duckduckgo"
    assert body["results"][0]["title"] == "DDG & Results"
    assert body["results"][0]["source"] == "d.example"  # netloc fallback
    assert calls["params"]["engine"] == "duckduckgo"


def test_searchapi_images_nested_shape(client, monkeypatch):
    _searchapi_env(monkeypatch)
    patch_get(monkeypatch, FakeResp(payload={"images": [
        {"title": "Otter", "original": {"link": "https://o.example/1.jpg"},
         "thumbnail": "https://t.example/1.jpg",
         "source": {"name": "Bing", "link": "https://p.example/1"}}]}))
    r0 = client.get("/api/search?q=otter&engine=duckduckgo&tab=images")\
             .get_json()["results"][0]
    assert r0["origin"] == "https://o.example/1.jpg"
    assert r0["thumb"] == "https://t.example/1.jpg"
    assert r0["source"] == "Bing"


def test_searchapi_places_coords_no_website(client, monkeypatch):
    _searchapi_env(monkeypatch)
    patch_get(monkeypatch, FakeResp(payload={"local_results": [
        {"title": "Sushi place", "address": "1 st", "rating": 4.2,
         "reviews": 9, "type": "sushi",
         "gps_coordinates": {"latitude": 34.7, "longitude": 135.5}}]}))
    r0 = client.get("/api/search?q=sushi&engine=google&tab=places")\
             .get_json()["results"][0]
    assert r0["coordinates"] == [34.7, 135.5]  # map pins work
    assert r0["url"] == ""                     # no website link in payload


def test_searchapi_priority_full_registry(client, monkeypatch):
    # All three google-capable providers configured: SerpApi wins (richest).
    _searchapi_env(monkeypatch, serpapi="sk", bd=True)
    posts = _patch_post(monkeypatch, {"organic": []})
    calls = patch_get(monkeypatch, FakeResp(payload={"organic_results": []}))
    client.get("/api/search?q=x&engine=google")
    assert "serpapi.com" in calls["url"]  # serpapi first (default URL)
    assert "url" not in posts


def test_searchapi_serves_bing_before_brightdata(client, monkeypatch):
    _searchapi_env(monkeypatch, bd=True)
    posts = _patch_post(monkeypatch, {"organic": []})
    calls = patch_get(monkeypatch, FakeResp(payload={"organic_results": []}))
    client.get("/api/search?q=x&engine=bing")
    assert "sa.test" in calls["url"] and "url" not in posts


def test_searchapi_duckduckgo_tab_guards(client, monkeypatch):
    _searchapi_env(monkeypatch)
    _patch_post(monkeypatch, {})
    assert client.get("/api/search?q=x&engine=duckduckgo&tab=places").status_code == 400
    assert client.get("/api/search?q=x&engine=duckduckgo&tab=news").status_code == 400


def test_config_lists_duckduckgo_engine(client, monkeypatch):
    monkeypatch.setattr(appmod, "API_KEY", "k")
    _searchapi_env(monkeypatch)
    engines = {e["id"]: e for e in client.get("/api/config").get_json()["engines"]}
    assert engines["duckduckgo"]["tabs"] == ["web", "images"]
    assert engines["duckduckgo"]["label"] == "DuckDuckGo"
    assert engines["bing"]["tabs"] == ["web", "news", "images"]


# --- Kagi provider (own engine pill; POST workflow API) ---

def _kagi_env(monkeypatch):
    monkeypatch.setattr(appmod, "KAGI_KEY", "kk")
    monkeypatch.setattr(appmod, "KAGI_URL", "http://kagi.test/api/v1/search")
    monkeypatch.setattr(appmod, "SERPAPI_KEY", "")
    monkeypatch.setattr(appmod, "SEARCHAPI_KEY", "")
    monkeypatch.setattr(appmod, "BD_KEY", "")
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")


def test_kagi_web_strips_markup(client, monkeypatch):
    _kagi_env(monkeypatch)
    posts = {}
    real_post = appmod.requests.post

    def fake_post(url, headers=None, json=None, timeout=None):
        posts["url"] = url; posts["headers"] = headers; posts["json"] = json
        class R:
            status_code = 200
            def json(self):
                return {"data": {"search": [
                    {"url": "https://a.example/1", "title": "T &amp; B",
                     "snippet": "clean <strong>baked</strong> text",
                     "time": "2026-10-08T10:00:00Z", "props": {"group_id": "a.example"}}]}}
        return R()
    monkeypatch.setattr(appmod.requests, "post", fake_post)
    body = client.get("/api/search?q=x&engine=kagi").get_json()
    r0 = body["results"][0]
    assert posts["url"] == "http://kagi.test/api/v1/search"
    assert posts["headers"]["Authorization"] == "Bearer kk"
    assert posts["json"]["workflow"] == "search"
    assert "!safe=moderate" in posts["json"]["query"]
    assert r0["title"] == "T & B"                    # entity-unescaped
    assert r0["description"] == "clean baked text"   # tags stripped
    assert r0["source"] == "a.example"
    assert r0["age"].endswith("ago")                 # ISO -> relative
    assert real_post


def test_kagi_news_workflow_and_images(client, monkeypatch):
    _kagi_env(monkeypatch)
    seen = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(json)
        class R:
            status_code = 200
            def json(self):
                wf = json["workflow"]
                data = {"news": [{"url": "https://n/1", "title": "News item",
                                  "time": "2026-10-10T08:00:00Z",
                                  "props": {"group_id": "n.example"}}]} \
                    if wf == "news" else \
                    {"image": [{"url": "https://p/1", "title": "Img",
                                "image": {"url": "https://img/1.jpg"},
                                "props": {"thumbnail": {"url": "https://t/1.jpg"}}}]}
                return {"data": data}
        return R()
    monkeypatch.setattr(appmod.requests, "post", fake_post)
    news = client.get("/api/search?q=x&engine=kagi&tab=news").get_json()["results"]
    assert seen["workflow"] == "news"
    assert news[0]["source"] == "n.example" and news[0]["age"].endswith("ago")
    imgs = client.get("/api/search?q=x&engine=kagi&tab=images").get_json()["results"]
    assert seen["workflow"] == "images"
    assert imgs[0]["origin"] == "https://img/1.jpg"
    assert imgs[0]["thumb"] == "https://t/1.jpg"


def test_kagi_tab_guard_and_config(client, monkeypatch):
    _kagi_env(monkeypatch)
    assert client.get("/api/search?q=x&engine=kagi&tab=places").status_code == 400
    engines = {e["id"]: e for e in client.get("/api/config").get_json()["engines"]}
    assert engines["kagi"]["tabs"] == ["web", "news", "images"]
    assert engines["kagi"]["label"] == "Kagi"


def test_kagi_error_status_only(client, monkeypatch):
    _kagi_env(monkeypatch)
    def fake_post(url, headers=None, json=None, timeout=None):
        class R:
            status_code = 401
            def json(self):
                return {"error": {"message": "bad key sk-secretish for query X"}}
        return R()
    monkeypatch.setattr(appmod.requests, "post", fake_post)
    body = client.get("/api/search?q=x&engine=kagi").get_json()
    assert body["error"] == "Kagi error (HTTP 401)."   # body never relayed


# --- quota-like upstream error surfacing (WIP heuristics) ---

class _R:
    def __init__(self, status, text="{}"):
        self.status_code = status
        self.text = text
    def json(self):
        return json.loads(self.text)


def _fake_upstream(monkeypatch, resp):
    monkeypatch.setattr(appmod.requests, "get", lambda *a, **k: resp)
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: resp)


def test_brave_429_surfaces_quota(client, monkeypatch):
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    _fake_upstream(monkeypatch, _R(429, '{"detail":"Quota exceeded"}'))
    r = client.get("/api/search?q=x")
    assert r.status_code == 429
    assert "quota or billing limit" in r.get_json()["error"]
    assert "Quota exceeded" not in r.get_json()["error"]  # body never relayed


def test_brave_500_stays_status_only(client, monkeypatch):
    _fake_upstream(monkeypatch, _R(500, '{"detail":"internal"}'))
    r = client.get("/api/search?q=x")
    assert r.status_code == 502
    assert r.get_json()["error"] == "Brave Search error (HTTP 500)."


def test_phrase_only_quota_detection_400(client, monkeypatch):
    # vendor returns 400 but body clearly says the meter is empty
    _serp_env(monkeypatch)
    _fake_upstream(monkeypatch, _R(400, '{"error":"You have no searches left"}'))
    r = client.get("/api/search?q=x&engine=google")
    assert r.status_code == 429
    assert "SerpApi" in r.get_json()["error"] and "quota or billing limit" in r.get_json()["error"]


def test_kagi_402_is_quota(client, monkeypatch):
    _kagi_env(monkeypatch)
    _fake_upstream(monkeypatch, _R(402, "payment required"))
    r = client.get("/api/search?q=x&engine=kagi")
    assert r.status_code == 429 and "quota or billing limit" in r.get_json()["error"]


def test_brightdata_429_is_quota(client, monkeypatch):
    monkeypatch.setattr(appmod, "BD_KEY", "bk")
    monkeypatch.setattr(appmod, "BD_ZONE", "bz")
    _fake_upstream(monkeypatch, _R(429, "not enough credits"))
    r = client.get("/api/search?q=x&engine=google")
    assert r.status_code == 429 and "Bright Data" in r.get_json()["error"]


def test_answers_quota_surfaced(client, monkeypatch):
    monkeypatch.setattr(appmod, "LOCAL_CHAT_URL", "http://local.test/v1")
    monkeypatch.setattr(appmod, "LOCAL_CHAT_KEY", "")
    monkeypatch.setattr(appmod, "LOCAL_CHAT_MODEL", "m")
    _fake_upstream(monkeypatch, _R(429, '{"error":{"message":"insufficient balance"}}'))
    r = client.post("/api/answer", json={"q": "x"})
    assert r.status_code == 429
    assert "quota or billing limit" in r.get_json()["error"]


def test_kagi_news_sorted_freshest_first(client, monkeypatch):
    _kagi_env(monkeypatch)
    payload = {"data": {"news": [
        {"url": "https://n/old", "title": "old", "time": "2026-06-01T00:00:00Z",
         "props": {"group_id": "n.example"}},
        {"url": "https://n/fresh", "title": "fresh", "time": "2026-10-10T06:00:00Z",
         "props": {"group_id": "n.example"}},
        {"url": "https://n/ref", "title": "evergreen page", "props": {}},
    ]}}
    class R:
        status_code = 200
        text = "{}"
        def json(self):
            return payload
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: R())
    rs = client.get("/api/search?q=x&engine=kagi&tab=news").get_json()["results"]
    assert [r["title"] for r in rs] == ["fresh", "old", "evergreen page"]


def test_kagi_news_freshness_is_a_real_cutoff(client, monkeypatch):
    _kagi_env(monkeypatch)
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    def ago(**kw):
        return (now - datetime.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {"data": {"news": [
        {"url": "1", "title": "recent", "time": ago(hours=5), "props": {}},
        {"url": "2", "title": "stale", "time": ago(days=40), "props": {}},
        {"url": "3", "title": "undated reference page", "props": {}},
    ]}}
    class R:
        status_code = 200
        text = "{}"
        def json(self):
            return payload
    monkeypatch.setattr(appmod.requests, "post", lambda *a, **k: R())
    rs = client.get("/api/search?q=x&engine=kagi&tab=news&freshness=week")\
               .get_json()["results"]
    assert [r["title"] for r in rs] == ["recent"]   # stale + undated cut
    rs = client.get("/api/search?q=x&engine=kagi&tab=news").get_json()["results"]
    assert [r["title"] for r in rs] == ["recent", "stale", "undated reference page"]


def test_empty_results_are_not_cached(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, hours=1)
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    calls = {"n": 0}
    def fake_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp(payload={"web": {"results": []}})
    monkeypatch.setattr(appmod.requests, "get", fake_get)
    r1 = client.get("/api/search?q=nothinghere").get_json()
    r2 = client.get("/api/search?q=nothinghere").get_json()
    assert r1.get("results") == [] and r2.get("results") == []
    assert not r1.get("cached") and not r2.get("cached")
    assert calls["n"] == 2          # re-ran upstream; empties never stick


def test_legacy_empty_cache_row_self_heals(client, monkeypatch, tmp_path):
    _enable_cache(tmp_path, monkeypatch, hours=168)
    db = str(tmp_path / "cache.db")
    monkeypatch.setattr(appmod, "SEARXNG_URL", "")
    # simulate pre-fix poison written directly to the DB
    appmod.cache_set("search|legacy-poison", {"results": [], "tab": "web"}, 168)
    calls = {"n": 0}
    def fake_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp(payload={"web": {"results": []}})
    monkeypatch.setattr(appmod.requests, "get", fake_get)
    with appmod._db() as conn:  # confirm the poison row exists
        assert conn.execute("SELECT 1 FROM cache WHERE key='search|legacy-poison'").fetchone()
    r = client.get("/api/search?q=toronto+election&tab=web&engine=searxng")
    # poison can't be hit by key (keys are hashes); verify the mechanism on
    # the seeded row itself instead: cache_get must purge and miss it
    assert appmod.cache_get("search|legacy-poison", 168) is None
    with appmod._db() as conn:
        assert not conn.execute("SELECT 1 FROM cache WHERE key='search|legacy-poison'").fetchone()
    assert not r.get_json().get("cached")  # live query also refuses to serve empty
