"use strict";

const STORAGE_KEY = "settings";
const DEFAULTS = {
  theme: "auto", safesearch: "moderate", count: 10, freshness: "any", spellcheck: true,
  enabled_answers: true, answers_freshness: "any",
  enabled_news: true, enabled_images: true, enabled_places: true,
  places_location: false, places_preset: "", map_embed: true, ui_scale: 100,
  engine: "brave", answers_engine: "brave",
};

const $ = (id) => document.getElementById(id);

function loadSettings() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveSettings(s) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
}

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = theme;
  } else {
    delete document.documentElement.dataset.theme;
  }
}

let settings = loadSettings();
applyTheme(settings.theme);

// Whole-UI zoom (75/100/125/150). CSS `zoom` is standardized in current
// Chromium/Firefox/Safari and scales px/vh-based styles for free.
// Phones get an extra 0.75 multiplier (user decision 2026-10: the desktop
// layout was too chunky for iPhone viewports); the user's ui_scale still
// layers on top of it.
const MOBILE_Q = "(max-width: 899px)";

function applyZoom() {
  const s = [75, 100, 125, 150].includes(Number(settings.ui_scale))
    ? Number(settings.ui_scale) : 100;
  const eff = window.matchMedia(MOBILE_Q).matches ? s * 0.75 : s;
  document.documentElement.style.zoom = String(eff / 100);
}
applyZoom();
try {
  window.matchMedia(MOBILE_Q).addEventListener("change", applyZoom);
} catch (e) {}  // older Safari without addEventListener on MediaQueryList

// --- Settings overlay ---
const overlay = $("settings-overlay");

function openSettings() {
  for (const key of Object.keys(DEFAULTS)) {
    const el = $(key);
    if (!el) continue;  // no control for this setting (e.g. engine pills)
    if (el.type === "checkbox") el.checked = Boolean(settings[key]);
    else el.value = String(settings[key]);
  }
  $("map_embed").disabled = !serverCaps.map_embed_allowed;
  syncPresetState();
  $("map_embed").disabled = !serverCaps.map_embed_allowed;
  overlay.hidden = false;
}

function syncPresetState() {
  // Preset input is greyed out while browser location wins.
  $("places_preset").disabled = Boolean(settings.places_location);
}

function closeSettings() {
  overlay.hidden = true;
}

// Green flash on the changed control (or its row) to signal autosave.
function flashSave(el) {
  const target = el.closest(".row") || el;
  target.classList.remove("flash");
  void target.offsetWidth; // restart the animation on repeat changes
  target.classList.add("flash");
}

$("gear").addEventListener("click", openSettings);
$("close-settings").addEventListener("click", closeSettings);
overlay.addEventListener("click", (e) => {
  if (e.target === overlay) closeSettings();
});

for (const key of Object.keys(DEFAULTS)) {
  if (!$(key)) continue;  // settings without a control (e.g. engine pills)
  $(key).addEventListener("change", () => {
    const el = $(key);
    if (el.type === "checkbox") settings[key] = el.checked;
    else if (key === "count") settings[key] = Number(el.value);
    else settings[key] = el.value;
    saveSettings(settings);
    flashSave(el);
    if (key === "theme") applyTheme(settings.theme);
    if (key === "ui_scale") applyZoom();
    if (key === "places_location") syncPresetState();
    if (key === "map_embed") settings.map_embed ? maybeRenderMap() : hideMap();
    if (key.startsWith("enabled_")) buildTabs();
  });
}

// --- Tabs ---
const tabsEl = $("tabs");
let serverCaps = { answers_enabled: false,
                   engines: [{ id: "brave", tabs: ["web", "news", "images", "places", "answers"] }] };
let currentTab = "web";

// --- Web engine picker (capability-driven: only engines configured on the
// server AND supporting the current tab are offered) ---
const ENGINE_LABELS = { brave: "Brave", kagi: "Kagi", duckduckgo: "DuckDuckGo", google: "Google", bing: "Bing" };

function enginesFor(tab) {
  return (serverCaps.engines || []).filter(e => (e.tabs || []).includes(tab));
}

function answersBackends() {
  // Answers backends carry their own user-facing label (e.g. "Local").
  return serverCaps.answers_backends || [];
}

function engineForAnswers() {
  const id = settings.answers_engine;
  if (answersBackends().some(e => e.id === id)) return id;
  return answersBackends()[0] ? answersBackends()[0].id : "brave";
}

function engineForRequest(tab) {
  const id = settings.engine;
  if (enginesFor(tab).some(e => e.id === id)) return id;
  const first = enginesFor(tab)[0];  // e.g. Brave-less server: never send an unconfigured id
  return first ? first.id : "brave";
}

function buildEngines() {
  const row = $("engines");
  row.innerHTML = "";
  const isAns = currentTab === "answers";
  const avail = isAns ? answersBackends() : enginesFor(currentTab);
  const active = isAns ? engineForAnswers() : engineForRequest(currentTab);
  if (avail.length < 2) { row.hidden = true; return; }
  for (const e of avail) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.engine = e.id;
    b.textContent = e.label || ENGINE_LABELS[e.id] || e.id;
    b.classList.toggle("active", e.id === active);
    b.addEventListener("click", () => {
      if (isAns) settings.answers_engine = e.id;
      else settings.engine = e.id;
      saveSettings(settings);
      buildEngines();
      flashSave(document.querySelector(`#engines button[data-engine="${e.id}"]`) || row);
    });
    row.append(b);
  }
  row.hidden = false;
}

function tabEnabled(tab) {
  // Query types follow the configured backends: a tab nobody can serve hides.
  const caps = serverCaps.engines;
  if (Array.isArray(caps) && caps.length && !caps.some(e => (e.tabs || []).includes(tab))) {
    return false;
  }
  if (tab === "web") return true;
  if (tab === "answers") return serverCaps.answers_enabled && settings.enabled_answers;
  return settings["enabled_" + tab];
}

function selectTabVisually(tab) {
  currentTab = tab;
  for (const btn of tabsEl.querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  }
  // Images uses Brave's stricter safesearch mapping; flag it in settings.
  $("ss-note").hidden = tab !== "images";
  $("places-opts").hidden = tab !== "places";
  buildEngines();
  updateLayout();
}

// Desktop split view for Places: map left, listings right (CSS grid does the
// actual layout once .places-mode is on <main>).
function updateLayout() {
  const on = currentTab === "places" && !$("map").hidden;
  document.querySelector("main").classList.toggle("places-mode", on);
  if (on && mapObj && typeof mapObj.invalidateSize === "function") {
    const m = mapObj;  // skip if hideMap()/re-render replaced it meanwhile
    requestAnimationFrame(() => { if (mapObj === m) m.invalidateSize(); });
  }
}

function buildTabs() {
  for (const btn of tabsEl.querySelectorAll("button")) {
    btn.hidden = !tabEnabled(btn.dataset.tab);
  }
  if (!tabEnabled(currentTab)) selectTabVisually("web");
}

for (const btn of tabsEl.querySelectorAll("button")) {
  btn.addEventListener("click", () => {
    // Tab switch only changes mode and clears the UI; the query runs on Go/Enter.
    if (btn.dataset.tab !== currentTab) {
      selectTabVisually(btn.dataset.tab);
      resultsEl.innerHTML = "";
      statusEl.textContent = "";
      hideMap();
      input.focus();
    }
  });
}

fetch("/api/config").then(r => r.json()).then(c => {
  serverCaps = c;
  $("map_embed").disabled = !c.map_embed_allowed;
  if (!c.map_embed_allowed) {
    // Server says no: hide the option entirely (not just greyed out).
    for (const el of document.querySelectorAll(".map-embed-only")) el.hidden = true;
  }
  if (!c.answers_enabled) {
    // Answers is a fully optional feature: hide tab AND settings section.
    for (const el of document.querySelectorAll(".answers-only")) el.hidden = true;
  }
  buildTabs();
  buildEngines();
  runDeepLink();
}).catch(() => { buildTabs(); buildEngines(); runDeepLink(); });

// --- Places geolocation (per-request, nothing stored) ---
let coordsCache = null;

function getCoords() {
  return new Promise((resolve) => {
    if (coordsCache) return resolve(coordsCache);
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (p) => { coordsCache = { lat: p.coords.latitude, lon: p.coords.longitude }; resolve(coordsCache); },
      () => resolve(null),
      { timeout: 5000, maximumAge: 300000 }
    );
  });
}

// --- Searching ---
const form = $("search-form");
const input = $("q");
const statusEl = $("status");
const resultsEl = $("results");
const goBtn = $("go");

async function search(query, tab = currentTab) {
  query = (query || "").trim();
  if (!query) return;

  input.value = query;
  if (!tabEnabled(tab)) tab = "web";
  selectTabVisually(tab);
  resultsEl.innerHTML = "";
  hideMap();
  statusEl.textContent = "Searching…";
  goBtn.disabled = true;

  // Shareable URL; Back/Forward steps through searches (popstate re-runs them).
  {
    const u = { q: query, tab };
    const eng = tab === "answers" ? engineForAnswers() : engineForRequest(tab);
    if (eng !== "brave") u.engine = eng;
    history.pushState(null, "", `/?${new URLSearchParams(u)}`);
  }

  try {
    if (tab === "answers") {
      await doAnswer(query);
      return;
    }

    const params = new URLSearchParams({
      q: query,
      tab,
      safesearch: settings.safesearch,
      count: String(settings.count),
    });
    if (tab === "web" || engineForRequest(tab) !== "brave") {
      const eng = engineForRequest(tab);
      if (eng !== "brave") params.set("engine", eng);
    }
    if (settings.freshness !== "any") params.set("freshness", settings.freshness);
    if (tab === "web" && !settings.spellcheck) params.set("spellcheck", "false");
    if (tab === "places") {
      // Priority: tab override > browser geolocation > preset location > none.
      const loc = $("loc").value.trim();
      const preset = settings.places_preset.trim();
      if (loc) {
        params.set("loc", loc);
      } else if (settings.places_location) {
        const c = await getCoords();
        if (c) {
          params.set("lat", c.lat);
          params.set("lon", c.lon);
        } else if (preset) {
          params.set("loc", preset); // fall back to preset on denial/unavailable
        } else {
          statusEl.textContent = "Location unavailable — searching Places without it.";
          return;
        }
      } else if (preset) {
        params.set("loc", preset);
      }
    }

    const resp = await fetch(`/api/search?${params}`);
    const data = await resp.json();
    if (!resp.ok) {
      statusEl.textContent = data.error || "Search failed.";
      return;
    }
    if (!data.results.length) {
      statusEl.textContent = "No results.";
      return;
    }
    statusEl.textContent = `${data.results.length} results`
      + (data.cached ? " (cached)" : "")
      + (data.altered_query ? ` — showing results for “${data.altered_query}”` : "");
    if (tab === "images") {
      renderImages(data.results);
      return;
    }
    resultsEl.classList.remove("img-grid");
    if (tab === "places") {
      renderPlaces(data.results);
      return;
    }
    renderList(data.results);
  } catch {
    statusEl.textContent = "Network error.";
  } finally {
    goBtn.disabled = false;
  }
}

async function doAnswer(query) {
  const eng = engineForAnswers();
  const label = (answersBackends().find(e => e.id === eng) || {}).label || "Answers";
  statusEl.textContent = `Asking ${label}…`;
  const resp = await fetch("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ q: query, freshness: settings.answers_freshness, engine: eng }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    statusEl.textContent = data.error || "Answers failed.";
    return;
  }
  statusEl.textContent = "Answer" + (data.cached ? " (cached)" : "");
  const li = document.createElement("li");
  li.className = "answer";
  const text = document.createElement("div");
  text.className = "answer-text";
  text.append(renderMarkdown(data.answer || "")); // safe DOM-building renderer, no innerHTML
  li.append(text);
  const cits = (data.citations || []).filter(u => /^https?:\/\//.test(u));
  if (cits.length) {
    const src = document.createElement("div");
    src.className = "citations";
    src.append("Sources: ");
    cits.forEach((u, i) => {
      const a = document.createElement("a");
      a.href = u;
      a.textContent = `[${i + 1}]`;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      src.append(a, " ");
    });
    li.append(src);
  }
  resultsEl.append(li);
}

function linkResult(r) {
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.href = r.url;
  a.textContent = r.title || r.url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  const url = document.createElement("div");
  url.className = "result-url";
  url.textContent = r.url;
  const desc = document.createElement("div");
  desc.className = "result-desc";
  desc.append(stripHtml(r.description)); // Brave snippets may contain <b> tags
  li.append(a, url, desc);
  if (r.age) {
    const age = document.createElement("div");
    age.className = "result-age";
    age.textContent = r.age;
    li.append(age);
  }
  return li;
}

function renderList(results) {
  for (const r of results) resultsEl.append(linkResult(r));
}

function renderImages(results) {
  imageSet = results;
  resultsEl.classList.add("img-grid");
  for (const [i, r] of results.entries()) {
    const item = document.createElement("li");
    const a = document.createElement("a");
    a.href = r.origin || r.url; // lightbox opens on click; href stays a sane fallback
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.dataset.lightbox = String(i);
    const img = document.createElement("img");
    img.src = r.thumb;
    img.alt = r.title;
    img.loading = "lazy";
    const cap = document.createElement("div");
    cap.className = "img-cap";
    cap.textContent = r.title || r.source;
    a.append(img);
    item.append(a, cap);
    resultsEl.append(item);
  }
}

// --- Lightbox (no external libs). Original via properties.url, thumb fallback. ---
let imageSet = [];
let lightboxEl = null;
let lightboxIdx = -1;

resultsEl.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-lightbox]");
  if (!a) return;
  e.preventDefault();
  openLightbox(Number(a.dataset.lightbox));
});

function openLightbox(i) {
  if (!imageSet[i]) return;
  lightboxIdx = i;
  if (!lightboxEl) {
    lightboxEl = document.createElement("div");
    lightboxEl.className = "lightbox";
    lightboxEl.addEventListener("click", (e) => { if (e.target === lightboxEl) closeLightbox(); });
    document.body.append(lightboxEl);
  }
  lightboxEl.innerHTML = "";
  const r = imageSet[i];
  const img = document.createElement("img");
  img.alt = r.title;
  img.src = r.origin || r.thumb;
  img.addEventListener("error", () => { // host blocks direct hotlinks
    if (img.src !== r.thumb) { img.src = r.thumb; note(); }
  });
  const bar = document.createElement("div");
  bar.className = "lb-bar";
  const cap = document.createElement("span");
  cap.textContent = `${r.title}  (${i + 1}/${imageSet.length})`;
  const links = document.createElement("span");
  for (const [href, label] of [[r.origin, "original"], [r.url, "source page"]]) {
    if (!href) continue;
    const a = document.createElement("a");
    a.href = href;
    a.textContent = label;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    links.append(a, " ");
  }
  const x = document.createElement("button");
  x.className = "icon-btn";
  x.textContent = "✕";
  x.setAttribute("aria-label", "Close");
  x.addEventListener("click", closeLightbox);
  bar.append(cap, links, x);
  const nav = document.createElement("div");
  nav.className = "lb-nav";
  for (const [label, d] of [["←", -1], ["→", 1]]) {
    const b = document.createElement("button");
    b.className = "icon-btn";
    b.textContent = label;
    b.addEventListener("click", () => stepLightbox(d));
    nav.append(b);
  }
  lightboxEl.append(img, bar, nav);
  lightboxEl.hidden = false;
  function note() {
    const n = document.createElement("div");
    n.className = "lb-note";
    n.textContent = "original blocked — showing preview";
    bar.prepend(n);
  }
}

function stepLightbox(d) {
  const n = (lightboxIdx + d + imageSet.length) % imageSet.length;
  openLightbox(n);
}

function closeLightbox() {
  if (lightboxEl) { lightboxEl.hidden = true; lightboxEl.innerHTML = ""; }
}

function renderPlaces(results) {
  const photos = [];
  for (const [i, r] of results.entries()) {
    const li = document.createElement("li");
    li.className = "place";
    const main = document.createElement("div");
    main.className = "place-main";

    let title;
    // Title is never a link: it focuses the pin (website lives in the
    // Details popover's Website button), so no tab juggling on click.
    title = document.createElement("span");
    title.className = "place-title";
    title.setAttribute("role", "button");
    title.tabIndex = 0;
    title.textContent = r.title;
    title.dataset.placeIdx = String(i);
    title.dataset.haspin = String(!!r.img || (Array.isArray(r.coordinates) && r.coordinates.length === 2));
    title.addEventListener("click", () => focusPin(i));
    title.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); focusPin(i); }
    });
    main.append(title);

    // Blurb line above the details flow: description, else first category.
    const blurb = r.description || (r.categories ? r.categories.split(",")[0] : "");
    if (blurb) {
      const b = document.createElement("div");
      b.className = "place-blurb";
      b.textContent = blurb;
      main.append(b);
    }

    const lines = [r.address, r.phone, r.categories, r.opening].filter(Boolean);
    const hasMap = Array.isArray(r.coordinates) && r.coordinates.length === 2;
    if (lines.length || r.url || hasMap) {
      const det = document.createElement("details");
      det.className = "place-pop";
      const sum = document.createElement("summary");
      sum.textContent = "Details";
      const body = document.createElement("div");
      body.className = "place-pop-body";
      det.append(sum, body);
      for (const line of lines) {
        const div = document.createElement("div");
        div.className = "place-line";
        div.textContent = line;
        body.append(div);
      }
      if (r.url) {
        const a = document.createElement("a");
        a.className = "site-btn";
        a.textContent = "Website";
        a.href = r.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        body.append(a);
      }
      if (hasMap) {
        const [lat, lon] = r.coordinates;
        const div = document.createElement("div");
        div.className = "place-line";
        const m = document.createElement("a");
        m.className = "maplink";
        m.textContent = "View on map";
        m.href = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`;
        m.target = "_blank";
        m.rel = "noopener noreferrer";
        div.append(m);
        body.append(div);
      }
      main.append(det);
    }
    li.append(main);

    if (r.img) {
      const idx = photos.length;
      photos.push({ title: r.title, url: r.url || "", thumb: r.img, origin: r.img });
      const fig = document.createElement("a");
      fig.className = "place-photo";
      fig.href = r.img;
      fig.target = "_blank";
      fig.rel = "noopener noreferrer";
      fig.dataset.lightbox = String(idx); // handled by the shared lightbox
      const img = document.createElement("img");
      img.src = r.img;
      img.alt = r.title;
      img.loading = "lazy";
      fig.append(img);
      li.prepend(fig); // flex: photo left, details right
    }
    resultsEl.append(li);
  }
  imageSet = photos; // lightbox set for this Places search
  resultsEl.classList.remove("img-grid");
  maybeRenderMap(results);
}

// Minimal, dependency-free safe Markdown subset: builds DOM nodes only.
// Supports **bold**, *italic*, `code`, [links](http(s)…), -/* bullets,
// 1. numbered lists, # headings. Links are https?://-only + noopener.
function mdInlineInto(el, str) {
  const re = /\*\*([^*]+)\*\*|\*([^*\n]+)\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let last = 0, m;
  while ((m = re.exec(str))) {
    if (m.index > last) el.append(str.slice(last, m.index));
    const [, b, i, c, a, u] = m;
    let node;
    if (b !== undefined) node = ["strong", b];
    else if (i !== undefined) node = ["em", i];
    else if (c !== undefined) node = ["code", c];
    else node = ["a", a, u];
    const el2 = document.createElement(node[0]);
    el2.textContent = node[1];
    if (node[2]) {
      el2.href = node[2];
      el2.target = "_blank";
      el2.rel = "noopener noreferrer";
    }
    el.append(el2);
    last = re.lastIndex;
  }
  if (last < str.length) el.append(str.slice(last));
}

function renderMarkdown(text) {
  const frag = document.createDocumentFragment();
  let list = null;
  for (const line of text.split("\n")) {
    const t = line.trim();
    let m;
    if ((m = /^[-*\u2022]\s+(.*)/.exec(t))) {
      if (!list || list.tagName !== "UL") { list = document.createElement("ul"); frag.append(list); }
      const li = document.createElement("li"); mdInlineInto(li, m[1]); list.append(li);
      continue;
    }
    if ((m = /^\d+[.)]\s+(.*)/.exec(t))) {
      if (!list || list.tagName !== "OL") { list = document.createElement("ol"); frag.append(list); }
      const li = document.createElement("li"); mdInlineInto(li, m[1]); list.append(li);
      continue;
    }
    list = null;
    if (!t) continue;
    const div = document.createElement("div");
    if ((m = /^#{1,6}\s+(.*)/.exec(t))) {
      div.className = "md-h";
      mdInlineInto(div, m[1]);
    } else {
      div.className = "md-p";
      mdInlineInto(div, t);
    }
    frag.append(div);
  }
  return frag;
}

// Render Brave's snippet (may contain <b> tags) as plain text.
function stripHtml(html) {
  const t = document.createElement("template");
  t.innerHTML = html || "";
  return t.content.textContent || "";
}

// --- Embedded map: DOUBLE opt-in (server config.allow_map_embed AND user cog).
// Until both are on, nothing here runs and no third-party requests are made.
// Leaflet is version-pinned from unpkg; tiles come from tile.openstreetmap.org
// (they see the mapped area + the visitor's IP) — hence the double gate.
let mapObj = null;
let lastPlaces = [];
let leafletLoading = null;
let pinMarkers = {};   // result index -> Leaflet marker

function mapOptIn() {
  return serverCaps.map_embed_allowed && settings.map_embed;
}

function hideMap() {
  const el = $("map");
  if (mapObj) { try { mapObj.remove(); } catch (e) {} mapObj = null; }
  pinMarkers = {};
  el.innerHTML = "";
  el.hidden = true;
  updateLayout();
}

function loadLeaflet() {
  if (window.L) return Promise.resolve();
  if (!leafletLoading) {
    leafletLoading = new Promise((resolve, reject) => {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      const s = document.createElement("script");
      s.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      s.onload = resolve;
      s.onerror = () => { leafletLoading = null; reject(new Error("leaflet failed to load")); };
      document.head.append(css, s);
    });
  }
  return leafletLoading;
}

function maybeRenderMap(results) {
  lastPlaces = results || lastPlaces;
  const el = $("map");
  if ($("places-opts").hidden) { hideMap(); return; }  // Places tab must be active
  if (!mapOptIn()) { hideMap(); return; }
  const pts = [];
  lastPlaces.forEach((r, i) => {
    if (Array.isArray(r.coordinates) && r.coordinates.length === 2) pts.push([i, r]);
  });
  if (!pts.length) { hideMap(); return; }
  el.hidden = false;
  updateLayout();
  loadLeaflet().then(() => {
    if (mapObj) { try { mapObj.remove(); } catch (e) {} }
    el.innerHTML = "";
    pinMarkers = {};
    mapObj = window.L.map(el);
    window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "\u00a9 OpenStreetMap contributors",
    }).addTo(mapObj);
    for (const [i, r] of pts) {
      const pop = document.createElement("div");
      pop.textContent = r.title; // DOM node popup: never an HTML string
      const mk = window.L.marker(r.coordinates).addTo(mapObj).bindPopup(pop);
      pinMarkers[i] = mk;
    }
    mapObj.fitBounds(window.L.latLngBounds(pts.map(([, r]) => r.coordinates)), { padding: [24, 24] });
  }).catch(() => {
    hideMap();
    statusEl.textContent += " Map library failed to load.";
  });
}

// Clicking a place's name (link or plain text) flies to and highlights its pin.
function focusPin(resultIdx) {
  const mk = pinMarkers[resultIdx];
  if (!mapObj || !mk) return;
  const ll = typeof mk.getLatLng === "function" ? mk.getLatLng() : null;
  if (ll) {
    if (typeof mapObj.flyTo === "function") mapObj.flyTo(ll, 16);
    else mapObj.setView(ll, 16);
  }
  if (typeof mk.openPopup === "function") mk.openPopup();
  const node = typeof mk.getElement === "function" ? mk.getElement() : null;
  if (node && node.classList) {
    node.classList.remove("pin-pop");
    void node.offsetWidth;
    node.classList.add("pin-pop");
  }
}

// Enter in the location override runs the Places search directly.
$("loc").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    search(input.value, "places");
  }
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  search(input.value);
});

// --- Deep links: /?q=...&tab=... (also handles browser Back/Forward) ---
function tabFromUrl(p) {
  const t = p.get("tab");
  const names = [...tabsEl.querySelectorAll("button")].map(b => b.dataset.tab);
  return names.includes(t) ? t : "web";
}

function applyEngineFromUrl(p) {
  // Deep links may pin the engine (e.g. /?q=x&tab=web&engine=google); runtime
  // only - the user's stored preference is left untouched.
  const e = p.get("engine");
  if (!e) return;
  if ((serverCaps.engines || []).some(x => x.id === e)) settings.engine = e;
  if ((serverCaps.answers_backends || []).some(x => x.id === e)) settings.answers_engine = e;
}

window.addEventListener("popstate", () => {
  const p = new URLSearchParams(location.search);
  const q = p.get("q");
  if (q) { applyEngineFromUrl(p); search(q, tabFromUrl(p)); }
});

function runDeepLink() {
  const p = new URLSearchParams(location.search);
  const q = p.get("q");
  if (q) { applyEngineFromUrl(p); search(q, tabFromUrl(p)); }
  else input.focus();
}

// --- Keyboard shortcuts ---
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && lightboxEl && !lightboxEl.hidden) {
    closeLightbox();
    return;
  }
  if (e.key === "Escape" && !overlay.hidden) {
    closeSettings();
    return;
  }
  if (lightboxEl && !lightboxEl.hidden && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    stepLightbox(e.key === "ArrowRight" ? 1 : -1);
    return;
  }
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (e.key === "/" && !typing) {
    e.preventDefault();
    input.focus();
    input.select();
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    const links = [...resultsEl.querySelectorAll("a")];
    if (!links.length) return;
    const i = links.indexOf(document.activeElement);
    if (i === -1 && typing) return; // arrows act on results, not inside the search box
    e.preventDefault();
    const next = e.key === "ArrowDown"
      ? links[Math.min(i + 1, links.length - 1)]
      : links[Math.max(i - 1, 0)];
    next.focus();
  }
});
