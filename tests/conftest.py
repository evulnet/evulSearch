"""Shared fixtures: mock Brave upstream + real app.py subprocess for UI tests."""
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent.parent

MOCK_KEY = "MOCK-BRAVE-KEY-do-not-ship"
MOCK_ANSWERS_KEY = "MOCK-BRAVE-ANSWERS-KEY-do-not-ship"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_mock_brave():
    """Deterministic stand-in for the Brave API (mirrors its real quirks)."""
    app = Flask("mock_brave")

    @app.get("/res/v1/web/search")
    def web():
        q = request.args.get("q", "")
        return jsonify(results={"web": {"results": [
            {"title": f"Web result for {q}", "url": "https://example.com/1",
             "description": "<b>bold</b> snippet", "age": "1 day ago"},
            {"title": "Second web result", "url": "https://example.com/2",
             "description": "plain", "age": ""},
        ]}})

    @app.get("/res/v1/news/search")
    def news():
        q = request.args.get("q", "")
        return jsonify(results=[
            {"title": f"News headline for {q}", "url": "https://news.example/1",
             "description": "news snippet", "age": "2 hours ago"},
        ])

    @app.get("/res/v1/images/search")
    def images():
        if request.args.get("safesearch") not in ("off", "strict"):
            return jsonify(error="bad safesearch enum"), 422  # mirrors real Brave
        return jsonify(results=[
            {"title": "Fox picture", "url": "https://img.example/1",
             "source": "img.example",
             # origin served by the mock itself: deterministic, offline-safe
             "properties": {"url": request.url_root + "fox-fullres.jpg"},
             "thumbnail": {"src": "https://picsum.photos/seed/fox/200/200"}},
        ])

    @app.get("/fox-fullres.jpg")
    def fox_fullres():
        # 1x1 transparent PNG
        import base64
        return app.response_class(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="),
            mimetype="image/png")

    @app.get("/v1/models")
    def llm_models():
        return jsonify(data=[{"id": "mock-model"}])

    @app.post("/v1/chat/completions")
    def llm_chat():
        body = request.get_json(force=True)
        assert body.get("model") == "mock-model"
        q = (body.get("messages") or [{}])[-1].get("content", "")
        return jsonify(choices=[{"message": {"role": "assistant",
                                "content": f"Local answer for {q}: 42."}}])

    @app.get("/serpapi/search.json")
    def serpapi():
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(request.full_path).query)
        assert qs.get("api_key", [""])[0] == "MOCK-SERPAPI-KEY"
        q = qs.get("q", [""])[0]
        eng = qs.get("engine", [""])[0]
        if eng == "google_news":
            return jsonify(news_results=[
                {"title": f"SerpApi news for {q}", "link": "https://sp.example/n1",
                 "snippet": "sn", "source": "sp.example", "date": "1 hour ago"}])
        if eng == "google_images":
            return jsonify(images_results=[
                {"title": f"SerpApi image {q}", "link": "https://sp.example/p1",
                 "thumbnail": "https://sp.example/i1.jpg",
                 "original": "https://sp.example/full.jpg", "source": "sp.example"}])
        if qs.get("fetch_place_results", [""])[0] == "true":
            # verified shape 2026-10: local_results.places[] + gps_coordinates
            return jsonify(local_results={"places": [
                {"title": f"SerpApi place for {q}", "address": "1 Osaka road",
                 "links": {"website": "https://sp.example", "phone": "tel:+81 00"},
                 "phone": "+81 00-0000", "rating": 4.4, "reviews": 240,
                 "type": "sushi", "hours": "Closes 11 PM",
                 "thumbnail_large": "https://sp.example/big.jpg",
                 "gps_coordinates": {"latitude": 34.7, "longitude": 135.5}}]})
        return jsonify(organic_results=[
            {"title": f"SerpApi result for {q}", "link": "https://sp.example/1",
             "snippet": "serp &amp; one", "displayed_link": "sp.example"},
            {"title": "Second SerpApi result", "link": "https://sp.example/2",
             "snippet": "serp two"},
        ])

    @app.get("/searxng/search")
    def searxng():
        if request.args.get("format") != "json":
            return "json disabled", 403
        q = request.args.get("q", "")
        cat = request.args.get("categories", "general")
        if cat == "news":
            return jsonify(results=[
                {"title": f"SearXNG news for {q}", "url": "https://sx.example/n1",
                 "content": "sx news", "published_date": "2026-10-10"}])
        if cat == "images":
            return jsonify(results=[
                {"title": f"SearXNG image {q}", "url": "https://sx.example/page1",
                 "img_src": "https://sx.example/i1.jpg",
                 "thumbnail": "https://sx.example/t1.jpg"}])
        return jsonify(results=[
            {"title": f"SearXNG result for {q}", "url": "https://sx.example/1",
             "content": "sx &amp; snippet"},
        ])

    @app.post("/kagi/search")
    def kagi():
        assert request.headers.get("Authorization") == "Bearer MOCK-KAGI-KEY"
        body = request.get_json(force=True)
        wf = body.get("workflow", "search")
        q = body.get("query", "")
        if wf == "news":
            return jsonify(data={"news": [
                {"url": "https://k.example/n1", "title": f"Kagi news for {q}",
                 "time": "2026-10-10T09:00:00Z",
                 "props": {"group_id": "k.example"}}]})
        if wf == "images":
            return jsonify(data={"image": [
                {"url": "https://k.example/page1", "title": f"Kagi image {q}",
                 "image": {"url": "https://k.example/full.jpg"},
                 "props": {"thumbnail": {"url": "https://p.kagi.example/t1.jpg"}}}]})
        return jsonify(data={"search": [
            {"url": "https://k.example/1", "title": f"Kagi result for {q}",
             "snippet": "stripped &amp; <strong>marked</strong> up",
             "time": "2026-10-01T00:00:00Z", "props": {"group_id": "k.example"}},
            {"url": "https://k.example/2", "title": "Second Kagi result",
             "snippet": "two", "props": {"group_id": "k.example"}},
        ]})

    @app.get("/searchapi/search")
    def searchapi():
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(request.full_path).query)
        assert qs.get("api_key", [""])[0] == "MOCK-SEARCHAPI-KEY"
        q = qs.get("q", [""])[0]
        eng = qs.get("engine", [""])[0]
        if eng.endswith("_images") or eng == "bing_images":
            return jsonify(images=[
                {"title": f"SearchApi image {q}",
                 "original": {"link": "https://sa.example/full.jpg"},
                 "thumbnail": "https://sa.example/i1.jpg",
                 "source": {"name": "ddgy", "link": "https://sa.example/p1"}}])
        if eng == "google" and request.args.get("q", "").startswith("sushi in"):
            # places ride the plain google engine (verified live 2026-10)
            return jsonify(local_results=[
                {"title": f"SearchApi place for {q}", "address": "1 Osaka road",
                 "rating": 4.2, "reviews": 130, "type": "sushi",
                 "gps_coordinates": {"latitude": 34.7, "longitude": 135.5}}])
        if eng.endswith("_news"):
            return jsonify(organic_results=[
                {"title": f"SearchApi news for {q}", "link": "https://sa.example/n1",
                 "snippet": "sn", "source": "sa.example", "date": "2 hours ago"}])
        return jsonify(organic_results=[
            {"title": f"SearchApi result for {q}", "link": "https://sa.example/1",
             "snippet": "sa &amp; one"},
            {"title": "Second SearchApi result", "link": "https://sa.example/2",
             "snippet": "sa two"},
        ])

    @app.post("/request")
    def bd_request():
        from urllib.parse import urlparse, parse_qs
        payload = request.get_json(force=True)
        assert request.headers.get("authorization") == "Bearer MOCK-BD-KEY"
        assert payload.get("zone") == "testzone"
        url = payload["url"]
        q = parse_qs(urlparse(url).query).get("q", [""])[0]
        if "tbm=nws" in url:
            return jsonify(news=[
                {"title": f"BD news for {q}", "link": "https://bd.example/n1",
                 "description": "news snippet", "source": "bd.example",
                 "time": "2 hours ago"},
            ])
        if "tbm=lcl" in url:
            return jsonify(snack_pack=[
                {"cid": "123", "name": f"BD place for {q}", "rating": 4.5,
                 "reviews_cnt": 123, "type": "ramen",
                 "address": "1 Test St", "image": "data:image/jpeg;base64,XX"},
                {"cid": "456", "name": "Second BD place", "type": "izakaya",
                 "address": "2 Test St"},
            ])
        if "tbm=isch" in url:
            return jsonify(images=[
                {"original_image": "https://bd.example/i1.jpg",
                 "image_alt": f"BD image {q}", "image": "data:image/jpeg;base64,BLOB",
                 "link": "https://google.com/goto?url=JUNK"},
                {"image_alt": "no original skipped"},
            ])
        return jsonify(organic=[
            {"title": f"BD result for {q}", "link": "https://bd.example/1",
             "description": "bd &amp; one", "display_link": "bd.example"},
            {"title": "Second BD result", "link": "https://bd.example/2",
             "description": "bd two", "display_link": "bd.example"},
        ])

    @app.post("/res/v1/chat/completions")
    def answers():
        if request.headers.get("x-subscription-token") != MOCK_ANSWERS_KEY:
            return jsonify(error="bad key"), 401
        body = request.get_json()
        q = body["messages"][0]["content"]
        md = (f"# Mock answer for: {q}\n\n"
              f"It is **bold** and *shifty* with `code`.\n\n"
              f"- point one\n- point two\n\n"
              f"See [details](https://cite.example/3). "
              f"<script>bad()</script> stays text.")
        payload = {
            "model": "brave-pro",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant",
                "content": md,
            }}],
            "citations": ["https://cite.example/1", "https://cite.example/2"],
        }
        return jsonify(payload)

    @app.get("/res/v1/local/place_search")
    def places():
        q = request.args.get("q", "")
        lat, lon = request.args.get("latitude"), request.args.get("longitude")
        near = f" near {lat},{lon}" if lat and lon else ""
        return jsonify(results=[
            {"title": f"Pizza place for {q}{near}", "url": "https://place.example",
             "description": "Wood-fired slices, cozy interior",
             "postal_address": {"displayAddress": "123 Main St"},
             "contact": {"telephone": "+000 000 000"},
             "opening_hours": {"current_day": [
                 {"full_name": "Saturday", "opens": "12:30", "closes": "00:30"}]},
             "pictures": [{"results": [{"src": request.url_root + "place.jpg"}]}],
             "coordinates": [37.9, 23.7], "categories": ["pizza"]},
            {"title": f"Second place for {q}{near}",  # no url: must not be a link
             "postal_address": {"displayAddress": "45 Side St"},
             "coordinates": [37.95, 23.75], "categories": ["cafe"]},
        ])

    @app.get("/place.jpg")
    def place_img():
        import base64
        return app.response_class(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="),
            mimetype="image/png")

    return app


@pytest.fixture(scope="session")
def mock_brave():
    port = _free_port()
    app = make_mock_brave()
    t = threading.Thread(
        target=lambda: app.run(port=port, host="127.0.0.1", threaded=True),
        daemon=True,
    )
    t.start()
    import time
    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/res/v1/web/search?q=x", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/res/v1"


@pytest.fixture(scope="session")
def app_server(mock_brave, tmp_path_factory):
    import os
    import time
    import urllib.request
    # hermetic config: cache off, db in tmp (never touches the developer's files)
    cfg = tmp_path_factory.mktemp("cfg") / "config.json"
    cfg.write_text(json.dumps({"cache_hours": -1, "cache_hours_news": -1,
                               "allow_map_embed": True,
                               "cache_db": str(cfg.parent / "ui-tests.db")}))
    port = _free_port()
    env = {
        **os.environ,
        "BRAVE_API_KEY": MOCK_KEY,
        "BRAVE_ANSWERS_KEY": MOCK_ANSWERS_KEY,
        "BRAVE_BASE": mock_brave,
        "BRIGHTDATA_SERP_API_KEY": "MOCK-BD-KEY",
        "BRIGHTDATA_SERP_ZONE": "testzone",
        "BRIGHTDATA_API_URL": mock_brave.removesuffix("/res/v1") + "/request",
        "SERPAPI_KEY": "MOCK-SERPAPI-KEY",
        "SERPAPI_SEARCH_URL": mock_brave.removesuffix("/res/v1") + "/serpapi/search.json",
        "SEARCHAPI_KEY": "MOCK-SEARCHAPI-KEY",
        "SEARCHAPI_SEARCH_URL": mock_brave.removesuffix("/res/v1") + "/searchapi/search",
        "KAGI_KEY": "MOCK-KAGI-KEY",
        "KAGI_SEARCH_URL": mock_brave.removesuffix("/res/v1") + "/kagi/search",
        "SEARXNG_URL": mock_brave.removesuffix("/res/v1") + "/searxng",
        "LOCAL_CHAT_BASE_URL": mock_brave.removesuffix("/res/v1") + "/v1",
        "EVUL_CONFIG_PATH": str(cfg),
        "HOST": "127.0.0.1",
        "PORT": str(port),
    }
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py")],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
