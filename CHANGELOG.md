# Changelog

All notable changes to evulSearch are documented here.
Format: latest first; sections `Added / Changed / Fixed`.

## Unreleased — 2026-10 (mobile & branding)

### Added
- Favicon: “evil magnifier” mark in SVG (`static/favicon.svg`) plus PNG
  renditions (`favicon-32.png`, `favicon-192.png`, `apple-touch-icon.png`,
  rasterized from the SVG via headless Chromium). Safari supports no SVG
  favicons at all and showed a letter placeholder — the PNG +
  `apple-touch-icon` links fix tab bars and iOS home-screen icons; the SVG
  link stays for browsers that prefer it.
- `CHANGELOG.md` (this file) — keep it updated with every meaningful change.
- Mobile rigs: `overflow-x: clip` plus `overflow-wrap: anywhere` on result,
  answer and Places text — the page may wrap, but never scrolls sideways.

### Added
- **SearchApi.io provider** (`SEARCHAPI_KEY`): new **DuckDuckGo engine pill**
  (web + images — SearchApi's plan has no DDG news/maps engines), a second
  provider for Google, and **Bing upgrades to web/news/images**. SearchApi
  Places carry GPS coordinates (Google-pill map pins now come from SerpApi
  *or* SearchApi). Default priorities: Google = SerpApi > SearchApi > Bright
  Data; Bing = SearchApi > Bright Data (both overridable via
  `engine_priority`). Heads-up: SearchApi is a commercial SERP vendor
  (~100 free searches/month; zero-retention is enterprise-tier only) —
  DuckDuckGo *results*, not a DuckDuckGo *relationship*.
- **Admin provider levers** in `config.json`: `engine_priority` (per-engine
  provider order; omitted provider disabled, `[]` disables the engine) and
  `provider_quotas` (`per_day`/`per_month`, UTC resets, counted in SQLite
  `provider_usage` — counts only, never query text). Quota-exhausted
  providers fall through to the next provider that can serve the tab, so a
  metered API degrades to the cheap one instead of failing; only an
  all-capped engine returns 429. Cache hits never count against quota.
  Missing BRAVE key is now a per-engine 503, not a global 500 — brave-less
  servers (BrightData/SerpApi/SearXNG only) are first-class.
- SerpApi and SearXNG **live-verified** against real services: SerpApi web/
  images/places (places via `fetch_place_results` -> `local_results.places`
  with GPS coordinates — map pins work for the Google pill), SearXNG
  web/news/images on a LAN instance. Fixed mappers to verified shapes
  (`images_results`, `thumbnail_large` for hotlinkable thumbs).
- Provider registry under engine ids: Google/Bing pills can be served by a
  priority list of providers — SerpApi (`SERPAPI_KEY`; web/news/images/places,
  Places carries coordinates so the embedded map works like Brave) first,
  Bright Data second; the UI sees one engine pill with merged capabilities and
  never knows which provider answered. Cache keys carry the provider so
  payloads never mix.
- SearXNG provider (`SEARXNG_URL`): self-hosted metasearch engine pill
  (web/news/images); friendly error when its settings.yml hasn't enabled
  `formats: [json]`.
- New tests: provider priority (SerpApi wins Google, BrightData untouched),
  Places coordinates, SearXNG mapping/403 hint, config capability unions,
  rewritten UI engine-switch flow (4 web pills, map renders via SerpApi pins).

### Changed
- README leads with an `Engines` section: Bright Data SERP featured as a
  first-class backend alongside Brave, capability table, and an explicit
  "more backend APIs will follow" roadmap note (the registry is the point).
- Mobile (viewport < 900px) renders at the equivalent of the 75% UI scale:
  a 0.75 multiplier composes with the user's `ui_scale` setting (so a
  phone user at 125% sees 93.75%). Desktop is unchanged. Tab bar padding
  tightened on phones so all five tabs fit a basic iPhone.

## 1.0 — 2026-10 (public launch)

Single squashed initial commit; history of the private development phase, in
brief:

### Added
- Flask proxy for the Brave Search API (Web/News/Images/Places) and Brave
  Answers; keys never reach the browser; error bodies are not relayed.
- Vanilla-JS SPA: tabs above the search box, no auto-query on tab switch,
  deep links (`/?q=&tab=&engine=`) with Back/Forward, `/` focus and
  ↑/↓ result-walking shortcuts, image lightbox (←/→/Esc), safe built-in
  Markdown renderer for answers (DOM-node only, no innerHTML with upstream
  text), themes (auto/light/dark) and per-user settings in localStorage,
  per-tab toggles, UI scale (75/100/125/150).
- Places: photo cards with lightbox, Details popover (address/phone/
  categories/opening hours), blurb line, titles that focus map pins instead
  of navigating; embedded map (Leaflet + OSM, double opt-OUT) with desktop
  split view (map left, listings right); geolocation + location override +
  preset priority chain.
- SQLite query cache (per-row TTL, lazy purge; news uncached by default;
  `-1`/`0`/hours semantics).
- Modular engines: Brave native + Bright Data SERP (Google web/news/images/
  places, Bing web) behind a capability registry; engine pills appear per
  tab only when >1 configured engine supports it; tabs nobody can serve
  hide. Local Answers: any OpenAI-compliant chat endpoint as a second
  Answers backend (model auto-detect, optional pin, admin-set pill label).
- Test suite: 66 tests — unit/functional against in-process mocks of Brave,
  Bright Data and the chat server; Playwright UI tests against a real Flask
  subprocess (Leaflet stubbed; no test ever touches the network).
- Bug pass before launch: explicit safe-search `off` flags for BD engines,
  engine fallback on Brave-less servers, model-list hardening, real engine
  deep links, hermetic unit tests.

### Docs
- README (setup, config table, privacy notes incl. plaintext-HTTP and
  BrightData-latency cautions, known API limits incl. free tiers as of
  2026-10), AGENTS.md for future agents, MIT LICENSE.
