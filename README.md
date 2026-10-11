# evulSearch

A small, privacy-focused self-hosted search service. One Flask process proxies
your queries through a **pluggable engine registry** — Brave native, plus
Google / Bing / DuckDuckGo via SERP vendors (SerpApi, SearchApi, Bright Data)
or your own SearXNG, and optional AI Answers (Brave or any OpenAI-compliant
endpoint) — so your API keys never touch the browser. Add the keys you have;
evulSearch shows exactly what your backends can do and nothing more.

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

Your engine choice is remembered **per query type**: pick Kagi for News and
Google for Images and each tab keeps its own last-used pill (in browser
localStorage — never the server). A tab you've never chosen falls back to the
first engine that can serve it, and a shared `?engine=` deep link applies to
that one search without disturbing what's stored.

| Engine | Providers (best-first) | Web | News | Images | Places | Answers |
|---|---|---|---|---|---|---|
| Brave | Brave Search API (native) | ✅ | ✅ | ✅ | ✅ | ✅ (own key) |
| Kagi | Kagi API (native, pay-as-you-go) | ✅ | ✅ | ✅ | – | – |
| DuckDuckGo | SearchApi | ✅ | – | ✅ | – | – |
| Google | SerpApi, SearchApi, Bright Data | ✅ | ✅ | ✅ | ✅† | – |
| Bing | SearchApi, Bright Data | ✅ | ✅ | ✅ | – | – |
| SearXNG | your own SearXNG instance | ✅ | ✅ | ✅ | – | – |
| Local | any OpenAI-compliant /v1 | – | – | – | – | ✅ |

† The embedded map renders for **any** engine whose Places results carry
coordinates: Brave yes, Google yes when SerpApi or SearchApi serves it, Bright
Data's local-pack scrape no (it links out to Google Maps instead).

One pill per engine id even when several providers can serve it: you pick
“Google”, the server picks the provider by priority and quota
([Provider levers](#provider-levers-priority--quotas)), and the answer carries
no provider name because it is an implementation detail of your deployment.

A note on naming: the DuckDuckGo engine means *DuckDuckGo results*, fetched for
you by SearchApi. There is no public DuckDuckGo API any more, so those results
arrive through a commercial SERP vendor like every other row here. DuckDuckGo's
privacy stance is a property of its index, not of the pipe you read it through.
SearchApi's zero-retention commitment is enterprise-tier only, so on a hobby
deployment assume your queries reach a third party. The guarantees evulSearch
itself makes — no keys in the browser, no logs, your server as the only
entry point — are unaffected either way.

Everything except Kagi has a free tier (as of October 2026): Brave has a free
monthly quota, SearchApi ~100 searches/month, SerpApi ~100/month, Bright Data
ships free credits, and SearXNG is self-hosted so free forever. **Kagi's API is
pay-as-you-go with no free tier** — it's the "buy the premium index" option,
billed per request through Kagi's own dashboard; set a `provider_quotas`
budget the moment you enable it. SearchApi's light free quota is exactly what
`provider_quotas` exists to manage. Check pricing pages for current numbers;
nothing here is sponsored.

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
- **Mobile tuck**: on phones, a search collapses the brand/gear row and the
  tab bar so results own the screen. To bring them back, tap the italic
  ⌕ evulSearch chip that appears left of the engine pills — deliberately
  unlike a pill. (No pull-down gesture: it collided UX-wise with iOS's own
  top-pull.) Desktop never tucks, and its pills row never shows the chip.
- **No iOS focus-zoom**: text fields hold a rendered 16px font-size at every
  ui_scale (iOS refuses to focus fields below that and won't zoom back out).
- **iOS hard-lock**: `html`/`body` are pinned fixed (iOS rubber-bands the
  document with plain `overflow: hidden`), touch-panning is confined to the
  results area and the settings sheet, and `overscroll-behavior: contain`
  stops inner bounces chaining out.
- **Rigid app shell**: the page itself never scrolls on any device. Header,
  tabs, engine pills and the search bar are always on screen (top-pinned by
  construction), and vertical scrolling happens only inside the results area —
  every tab, mobile and desktop alike. Places on desktop (≥900px) is a split
  view: the map holds its full height on the left while the listings scroll
  independently. Phones keep the single-column layout. Image tiles are larger
  on desktop; the lightbox uses ~90% of the viewport with edge-mounted arrows.
- **Pluggable search engines** — see [Engines](#engines) above. Engine choice
  is per-user (localStorage), deep-linkable (`&engine=`), and gracefully
  falls back when an engine can't serve the current tab. The embedded map
  works for **any engine whose Places results carry coordinates** (Brave and
  SerpApi do; Bright Data's scrape doesn't — its Places link out to Google
  Maps instead). First Bright Data Google Images/Places hits are slow
  (~30–90 s live scrape); the query cache makes repeats instant.
- **Admin provider levers** — when one engine has several providers (Google =
  SerpApi and/or Bright Data), `engine_priority` sets the order and can
  disable either; `provider_quotas` gives metered APIs a call budget with
  automatic fall-through to the unmetered provider. See
  [Provider levers](#provider-levers-priority--quotas).
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
| `cache_db` | `evulsearch.db` | Path of the SQLite cache file (relative to `app.py` unless absolute). Also holds the `provider_usage` quota counters when quotas are configured (counts only — never query text) |
| `engine_priority` | built-in order | Provider order per engine; omit a provider to disable it for that engine, use `[]` to disable the engine. See [Provider levers](#provider-levers-priority--quotas) |
| `provider_quotas` | off | Call budgets per provider, e.g. `{"serpapi": {"per_day": 20, "per_month": 100}}`. UTC resets; exhausted providers fall through to the next provider. See [Provider levers](#provider-levers-priority--quotas) |

If `config.json` is missing or invalid, defaults apply. Restart the server
after editing.

### Provider levers: priority & quotas

One engine pill (say Google) can be served by several upstream providers.
Two config levers decide which one answers and how often, so the admin
chooses richer vs cheaper knowingly, per engine:

```json
{
  "engine_priority": {
    "google": ["brightdata", "serpapi"],
    "bing": ["brightdata"],
    "duckduckgo": [],
    "brave": ["brave"],
    "searxng": ["searxng"]
  },
  "provider_quotas": {
    "serpapi": {"per_day": 20, "per_month": 100}
  }
}
```

Valid provider names: `brave`, `kagi`, `serpapi`, `searchapi`,
`brightdata`, `searxng`.

**`engine_priority`** — provider order per engine:
- The first provider that supports the tab **and is under its quota** answers.
- Providers omitted from a non-empty list are **disabled for that engine**.
  The example puts Bright Data first for Google: SerpApi is overflow, not
  default.
- An **empty list disables the engine entirely**: the pill disappears from the
  UI and direct API calls return 503.
- Unset engines keep the built-in order: Google = SerpApi, SearchApi, Bright
  Data (SerpApi first because its Places carry website/phone/opening hours);
  Bing = SearchApi, Bright Data (SearchApi also serves Bing news/images);
  Kagi and DuckDuckGo each have a single provider, so their order matters
  only as an on/off switch.
- Unknown provider names are dropped.

**`provider_quotas`** — call budgets per provider (all providers, Brave
included):
- `per_day` and/or `per_month`, reset on UTC day/month boundaries. Counts
  persist in the `provider_usage` table of `cache_db` — counts only, never
  query text.
- On exhaustion the request **falls through to the next provider that can
  serve the tab**; a metered API degrades instead of failing. If every
  covering provider is capped out, the search returns a 429 naming the
  quotas hit.
- **Cache hits never count against quota.** Pairing a long `cache_hours` with
  a small metered budget is a deliberate strategy: repeats are free, each
  distinct query spends the meter once.
- Enabling quotas creates the SQLite file for counters even when
  `cache_hours: -1` turned the query cache off.

Strategies that combine well: *hoard a small SerpApi plan* —
`"provider_quotas": {"serpapi": {"per_day": 5}}` buys 5 map-pinned Google
Places searches a day and sends everything else to cheaper providers;
*stretch SearchApi's ~100/month* — quota it at 90 so the last 10 are always
available for the DuckDuckGo pill rather than silently spent on Google.

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

Every supported backend has a free tier (as of October 2026), so you can run
evulSearch end to end without paying anything; see [Engines](#engines) for the
numbers. Check the vendors' pricing pages for current figures; nothing here is
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
| `BRAVE_API_KEY` | recommended | Web/News/Images/Places Search API + Brave Answers. Optional only if other engines cover your needs |
| `BRAVE_ANSWERS_KEY` | no | Answers (separate Brave product; tab hides when unset) |
| `BRIGHTDATA_SERP_API_KEY` | no | Google (Web/News/Images/Places) + Bing (Web) engines via Bright Data SERP |
| `BRIGHTDATA_SERP_ZONE` | with BD key | Your Bright Data zone name (required by the API) |
| `SERPAPI_KEY` | no | SerpApi provider for the Google pill (web/news/images/**places-with-map-pins**); first priority for Google |
| `KAGI_KEY` | no | Kagi Search API (the Kagi pill: web/news/images; its own index). Pay-as-you-go, no free tier — pair with `provider_quotas` |
| `SEARCHAPI_KEY` | no | SearchApi.io provider: the DuckDuckGo engine (web/images) plus Google (all tabs) and Bing (web/news/images). Free tier ~100/mo — pair with `provider_quotas` |
| `SEARXNG_URL` | no | Base URL of a self-hosted SearXNG (web/news/images); its settings.yml must allow `formats: [json]` |
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
- **Your queries go to whichever vendors you configure.** Brave, Kagi, SerpApi,
  SearchApi and Bright Data each see the query text and your server's address;
  none of them are "private by contract" on hobby tiers (SearchApi's
  zero-retention offer is enterprise-tier only). SERP vendors exist to sell
  search results — treat them like any third party. If vendor-free is the
  goal, the only option today is your own SearXNG instance (and even that
  fetches from upstream engines). Brave's own API is the most privacy-
  respectful of the hosted options.
- **Bright Data-backed searches (when Bright Data serves the pill) can be slow and may time
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
  Empty result sets are never cached — a transient upstream hiccup (e.g. a
  SearXNG engine temporarily suspended for rate limits) should cost a retry,
  not a week of blank pages.
  Expired entries are purged lazily on the next request. Set `cache_hours` and
  `cache_hours_news` to `-1` in `config.json` if you do not want any query
  persistence at all (no database file is even created in that mode).
- Query text is trimmed of leading/trailing whitespace before caching, so
  `"evulSearch "` and `"evulSearch"` share one cache entry.
- All UI settings are stored in your browser's localStorage; clear them anytime.
- Upstream text is rendered as plain text or via the built-in safe markdown
  subset (no HTML injection), and outbound links use `rel="noopener noreferrer"`.

## Known API limits (verified live)

Quota-like upstream errors *(work in progress)*:
- When a provider says it is out of quota, credits, or billing, the search
  fails with **“<provider>: quota or billing limit reached upstream”** and
  HTTP 429, instead of an opaque “HTTP 400/402” note. Vendors do not agree on
  what that response looks like, so this is heuristic: we treat HTTP 402/429
  as quota, plus a deliberately narrow phrase scan of the error body
  (“quota”, “credits”, “no searches left”, “payment required”, …). The body
  itself is still never relayed — only the classification.
- Being a work in progress in both directions: false positives (a 429 that is
  really a temporary rate limit gets the quota wording) and false negatives
  (a quota failure wrapped in a plain 400 without the known phrases looks
  like a generic error) are both possible. If you see one mislabeled, note
  the provider and HTTP status — the whole classifier is one small table in
  `app.py` meant to be tuned as real failures are observed.
- Local quota you set yourself via `provider_quotas` is *not* this heuristic:
  those 429s are exact and always fall through to your other providers first.

Kagi:
- No Places tab (Kagi has no local-pack product). The pill shows Web/News/
  Images only.
- News comes back relevance-ranked from a sometimes-shallow pool (low-
  coverage queries surface months-old items; that is Kagi's index, not a
  parse failure). The proxy therefore **re-sorts news freshest-first** and,
  when you pick a Freshness value, enforces it with a true timestamp cutoff
  server-side — the `tbs` operator is inert for the news workflow (verified
  live). Undated reference pages sink last (and are excluded when a freshness
  filter is active), so a Kagi news tab never quietly opens on 3-month-old
  stories.
- Freshness on the Web tab rides the (undocumented) `tbs` operator — treated
  as best-effort upstream filtering.
- Snippets arrive with HTML markup and ISO timestamps; the proxy strips tags
  and converts times to relative ages before the browser ever sees them.
- Safe search uses the `!safe=` Teletype token in the query text.

Brave:
- ≤ 20 results per query; real pagination is not offered (`offset` capped).
- Images safe search only supports on/off (moderate is coerced to strict).
- Answers model is pinned by Brave (currently `brave-pro`) regardless of request.
- Places location override is composed into the query (`<q> in <loc>`); Brave
  has no structured geocoding parameter on this endpoint.

SearchApi (DuckDuckGo engine; Google/Bing backup provider):
- DuckDuckGo serves Web and Images only — SearchApi has no DDG news/maps
  engines on this plan (verified live), so the DuckDuckGo pill never shows
  News or Places tabs.
- DuckDuckGo Places coordinates exist in SearchApi's *maps* product, which
  this plan does not include — do not re-add `duckduckgo_maps` to caps
  without probing first.
- Google Places via SearchApi carry GPS coordinates but **no website link**;
  the card's title focuses the map pin, and there is no Website button for
  those results (SerpApi-filled Google results do have one).

Bright Data (when it serves the Google/Bing pills):
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
