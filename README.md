# evulSearch

A small, privacy-focused self-hosted search service. One Flask process proxies
your queries through a **pluggable engine registry** — Brave's native API and/or
Google & Bing via the **Bright Data SERP API** — so your API keys never touch
the browser. Add the keys you have; evulSearch shows exactly what your backends
can do and nothing more.

No cookies, no analytics. Results are cached server-side for faster repeat
searches (configurable, off-switch available). Designed to run on a private
network (for example behind WireGuard) and never be publicly exposed.

## Engines

Backends are modules, not conditionals: every engine registers which query
types it can serve, and the UI is generated from that — engine pills appear
per tab only when your configured keys make a real choice possible, and tabs
no backend can serve hide themselves. **More backend APIs will follow**; an
engine is a capability line plus a URL builder and a mapper, and adding one
means touching none of the others.

| Engine | Backend | Web | News | Images | Places | Answers |
|---|---|---|---|---|---|---|
| Brave | native Search API | ✅ | ✅ | ✅ | ✅ | ✅ (own key) |
| Google | Bright Data SERP | ✅ | ✅ | ✅ | ✅* | – |
| Bing | Bright Data SERP | ✅ | – | – | – | – |
| Local | any OpenAI-compliant /v1 | – | – | – | – | ✅ |

\* Google Places arrive without coordinates, so the embedded map stays empty
for that engine (results still link out to Google Maps).

Bright Data's SERP API has a free tier (as of 10/2026) alongside Brave's free
quota — you can run the whole multi-engine stack for nothing to try it out.

## Features

- Web, News, Images and Places search, plus **Answers** (optional): Brave AI
  answers and/or any OpenAI-compliant chat endpoint (vLLM, Ollama, hosted) —
  when both are configured, the Answers tab shows Brave/“Local” pills to pick
- Single search box (Enter or Go) with Web/News/Images/Places/Answers tabs **above** it, visible on load (Web preselected); tabs are mode switches only (they clear the UI; Go/Enter runs the search)
- Settings popover (gear icon) — stored **only** in your browser's localStorage
  - Theme: auto / light / dark (auto follows your OS)
  - Safe search, results per search (max 20 — Brave's hard cap), time filter
  - Spell check toggle (web), per-tab enable switches
  - Answers time filter; Answers tab hides automatically when the server has no answers key
  - Places location resolution, highest priority first: location typed on the
    Places tab → browser geolocation (opt-in checkbox) → preset location text
    field in settings (greyed out while the checkbox is on) → plain search.
    Nothing location-related is ever stored server-side; geolocation is never
    persisted at all.
- Answers render as safe HTML from a built-in minimal Markdown subset (bold,
  italics, code, links, lists, headings) — upstream text is turned into DOM
  nodes, never raw HTML injection
- Deep-linkable results (`/?q=…&tab=…`), Back/Forward re-runs searches
- Keyboard shortcuts: `/` focus search, `↑`/`↓` walk results, `Esc` close settings
- Click any image result to open it in a built-in **lightbox**: shows the
  publisher’s original full-res image (with graceful fallback to the preview if
  the host blocks hotlinks), source-page link, `←`/`→` to walk the set, `Esc`
  or click-outside to close. No external libraries.
- Places results render as cards: photo when Brave has one (click → lightbox),
  and a “Details” popover carrying address, phone, categories, today’s opening
  hours and Brave’s description, plus a Website button (only when a site
  exists) and a “View on map” link (OpenStreetMap, opens on click). Clicking a
  result’s title never navigates — it focuses its map pin. A blurb line
  (description, falling back to the first category) shows under every title.
  Results without a website are plain text, never a dead link.
- **UI scale** (75 / 100 / 125 / 150 %, default 100) under Appearance scales
  the whole interface.
- **Embedded map (default ON)** under Places results. Clicking a place’s name
  flies the map to its pin, opens its popup and pulses it. Opt out per-user via
  the settings cog, or server-wide by setting `allow_map_embed: false` in
  `config.json` (the option then disappears from the cog entirely). Note the
  privacy implication: OSM tile servers see the mapped area and visitor IPs.
- Desktop (≥900px): Places uses a split view — sticky map on the left,
  listings on the right; image tiles are larger; the lightbox uses ~90% of the
  viewport with edge-mounted arrows. Phones keep the single-column layout.
- **Pluggable search engines** — see [Engines](#engines) above. Engine choice
  is per-user (localStorage), deep-linkable (`&engine=`), and gracefully
  falls back when an engine can't serve the current tab. Google Images grids
  hotlink originals (no thumbnail URLs in the SERP payload); Google Places
  link out via Google Maps. First Google Images/Places hits are slow
  (~30–90 s live scrape); the query cache makes repeats instant.
- Server capabilities drive the UI: with no answers key the Answers tab and its
  settings hide themselves; without `allow_map_embed` the embedded-map option is
  hidden too (not just greyed out).
- Server-side query cache (SQLite): searches and answers are cached and served
  with a “(cached)” marker on repeat. News is **not** cached by default (it
  goes stale fast) and has its own knob. See `config.example.json`
- Responsive layout for mobile and desktop

## Configuration

Copy `config.example.json` to `config.json` (gitignored) to change behavior:

| Key | Default | Meaning |
|---|---|---|
| `cache_hours` | `168` | Cache lifetime in hours for web/images/places/answers. `-1` disables that cache entirely, `0` keeps entries forever, positive numbers expire after that many hours. Negative values other than `-1` are treated as `-1`. |
| `cache_hours_news` | `-1` | Same semantics, but applies to news searches separately (news changes fast, so off by default). |
| `allow_map_embed` | `true` | Server half of the embedded-map switch (opt-OUT model: default on). Set `false` to disable map embedding for everyone — the cog option disappears and no third-party map assets are ever loaded (Leaflet from unpkg, tiles from OpenStreetMap, which see the mapped area + the visitor’s IP). Users can also opt out individually via the cog. |
| `cache_db` | `evulsearch.db` | Path of the SQLite cache file (relative to `app.py` unless absolute) |

If `config.json` is missing or invalid, defaults apply. Restart the server
after editing.

## How it works

The browser talks only to this server. The server attaches the Brave key
server-side and returns a sanitized subset of each Brave response. Brave error
bodies are never relayed to clients.

```
browser ──HTTP──> Flask (app.py) ──HTTPS──> Brave Search API
                    keys in env/.env only
```

## Setup

Requires Python 3.11+.

Both upstream search providers currently have free tiers (as of October 2026):
the Brave Search API offers a free monthly quota, and Bright Data's SERP API
includes free credits — so you can try evulSearch end to end without paying
anything. Check their pricing pages for current numbers; nothing here is
sponsored.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then edit .env
.venv/bin/python app.py     # http://127.0.0.1:5000
```

`.env` values:

| Variable | Required | Purpose |
|---|---|---|
| `BRAVE_API_KEY` | recommended | Web/News/Images/Places Search API + Brave Answers. Optional only if Bright Data covers your needs |
| `BRAVE_ANSWERS_KEY` | no | Answers (separate Brave product; tab hides when unset) |
| `BRIGHTDATA_SERP_API_KEY` | no | Google (Web/News/Images/Places) + Bing (Web) engines via Bright Data SERP |
| `BRIGHTDATA_SERP_ZONE` | with BD key | Your Bright Data zone name (required by the API) |
| `LOCAL_CHAT_BASE_URL` | no | OpenAI-compliant answers server, e.g. `http://127.0.0.1:8000/v1` (enables the “Local” answers pill) |
| `LOCAL_CHAT_API_KEY` | no | Bearer token, only sent if set (many local servers need none) |
| `LOCAL_CHAT_MODEL` | no | Pin a model id; default is auto-detect via `GET /models` |
| `LOCAL_CHAT_LABEL` | no | Pill label (default `Local`; rename it to whatever you call your box — the pill shows this, it is per-deployment since the endpoint is the admin's choice) |
| `HOST` | no | Bind address, default `127.0.0.1`. `0.0.0.0` to expose on your network |
| `PORT` | no | Default `5000` |

To serve to devices on your network/VPN: `HOST=0.0.0.0 .venv/bin/python app.py`
and reach it over your private network (e.g. a WireGuard peer address). Anyone
who can reach the port can spend your API quota, so keep it private. This is the
Flask development server; do not expose it to the public internet.

## Tests

Unit/functional tests (no network: Brave, Bright Data and the OpenAI-compliant
chat server are all mocked in-process):

```bash
.venv/bin/python -m pytest tests/test_app.py -q
```

Browser UI tests (Playwright + Chromium; installs a pinned browser on first run):

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/test_ui.py -q
```

UI tests drive a real Flask subprocess against a deterministic mock upstream
server (Brave + Bright Data + chat completions endpoints); no CDN, tile server
or search API is ever contacted from the test suite.

## Privacy notes

- **Local answers over plaintext `http://` — caution.** `LOCAL_CHAT_BASE_URL`
  accepts an `http://` URL so you can point at a LAN inference box without
  TLS setup. Understand the trade-off first: over plaintext HTTP your
  questions *and* the answers travel unencrypted across every network hop,
  visible to anything with access to that traffic (router, switches, a noisy
  neighbor). Only do this on a network you control. Prefer `https://` or a
  tunnel for anything beyond the LAN. When `LOCAL_CHAT_API_KEY` is set, that
  token also travels in a Bearer header and would be sniffable over HTTP.
- **Bright Data-backed searches (Google/Bing pills) can be slow and may time
  out.** BrightData scrapes the live results page rather than querying an API,
  so first-hit latency is a real browse: ~5 s for web, ~30 s for Google
  Images, and 60–90 s for Google Places (Google's local-pack payload embeds
  base64 photos for every venue; there is no documented flag to exclude them
  and `parsed_light` mode returns nothing for Places/Images). The proxy waits
  120 s before giving up — beyond that (or if BrightData itself gives up) you
  will see “Bad response from Bright Data SERP API” or a timeout. Repeats are
  served from the query cache instantly, so a failed slow search is usually
  worth retrying once. Brave-backed queries are unaffected (typically <1 s).
- Keys live only in the server's environment; they are never sent to the browser.
- **The server caches queries and their sanitized results** in the SQLite file at
  `cache_db` (queries are therefore visible to anyone with access to that file).
  Expired entries are purged lazily on the next request. Set `cache_hours` and
  `cache_hours_news` to `-1` in `config.json` if you do not want any query
  persistence at all (no database file is even created in that mode).
- Query text is trimmed of leading/trailing whitespace before caching, so
  `"evulSearch "` and `"evulSearch"` share one cache entry.
- All UI settings are stored in your browser's localStorage; clear them anytime.
- Upstream text is rendered as plain text or via the built-in safe markdown
  subset (no HTML injection), and outbound links use `rel="noopener noreferrer"`.

## Known API limits (verified live)

Brave:
- ≤ 20 results per query; real pagination is not offered (`offset` capped).
- Images safe search only supports on/off (moderate is coerced to strict).
- Answers model is pinned by Brave (currently `brave-pro`) regardless of request.
- Places location override is composed into the query (`<q> in <loc>`); Brave
  has no structured geocoding parameter on this endpoint.

Bright Data (Google/Bing pills):
- Google Places (`tbm=lcl`) carries no coordinates, so browser geolocation is
  ignored there and the map stays empty — put the place in the query or the
  Places location box instead. Bing serves the Web tab only.
- Bing ignores the time filter (its freshness syntax proved brittle; unmapped).
- Google image tiles hotlink full-size originals — the SERP payload exposes no
  thumbnail URLs.
- See “Bright Data-backed searches… can be slow” under Privacy notes for the
  latency/timeout behavior.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).
