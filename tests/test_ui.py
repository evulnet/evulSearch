"""Playwright UI tests: drive the real page in Chromium against app.py + mock Brave."""
import re

from playwright.sync_api import expect

from conftest import MOCK_KEY


def page_errors():
    return []


def new_page(browser, app_server, errors):
    page = browser.new_page()
    page.on("pageerror", lambda e: errors.append(e))
    return page


def test_search_by_enter_and_go_button(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")

    page.fill("#q", "kittens")
    page.press("#q", "Enter")
    expect(page.locator("#results li").first).to_contain_text("Web result for kittens")
    expect(page.locator("#status")).to_have_text("2 results")

    # snippets are rendered as plain text, never raw HTML
    expect(page.locator(".result-desc").first).to_have_text("bold snippet")

    # Go button path
    page.fill("#q", "puppies")
    page.click("#go")
    expect(page.locator("#results li").first).to_contain_text("Web result for puppies")

    # title + branding
    expect(page).to_have_title("evulSearch")
    expect(page.locator(".brand")).to_have_text("evulSearch")

    # the Brave key never appears anywhere in the DOM
    assert MOCK_KEY not in page.content()
    assert not errors
    page.close()


def test_tabs_news_and_images(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")
    page.fill("#q", "space")
    page.press("#q", "Enter")
    expect(page.locator("#results li").first).to_contain_text("Web result for space")

    # tab switch clears the UI and waits for Go (no auto-query)
    page.click('#tabs button[data-tab="news"]')
    expect(page.locator("#results li")).to_have_count(0)
    expect(page.locator("#status")).to_have_text("")
    expect(page.locator('#tabs button[data-tab="news"]')).to_have_class("active")
    page.click("#go")
    expect(page.locator("#results li").first).to_contain_text("News headline for space")

    page.click('#tabs button[data-tab="images"]')
    expect(page.locator("#results li")).to_have_count(0)
    page.press("#q", "Enter")
    expect(page.locator("#results.img-grid img")).to_have_count(1)
    expect(page.locator("#results .img-cap")).to_have_text("Fox picture")

    assert not errors
    page.close()


def test_settings_theme_persists_and_dark_mode_applies(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")

    # open overlay via gear, set dark
    page.click("#gear")
    expect(page.locator("#settings-overlay")).to_be_visible()
    page.select_option("#theme", "dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.keyboard.press("Escape")
    expect(page.locator("#settings-overlay")).to_be_hidden()

    # persists across reload
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    # background actually changed (dark token applied)
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(17, 19, 22)"

    # set back to auto -> attribute removed
    page.click("#gear")
    page.select_option("#theme", "auto")
    page.reload()
    expect(page.locator("html")).not_to_have_attribute("data-theme", "dark")
    assert not errors
    page.close()


def test_deep_link_and_back_button(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)

    # direct load of a shareable URL
    page.goto(app_server + "/?q=deep%20link&tab=news")
    expect(page.locator("#results li").first).to_contain_text("News headline for deep link")
    expect(page.locator("#q")).to_have_value("deep link")

    # a new search on the Web tab, then Back returns to the news deep link
    page.click('#tabs button[data-tab="web"]')
    page.fill("#q", "second")
    page.click("#go")
    expect(page.locator("#results li").first).to_contain_text("Web result for second")
    page.go_back()
    expect(page.locator("#results li").first).to_contain_text(
        "News headline for deep link"
    )
    assert not errors
    page.close()


def test_keyboard_shortcuts(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")

    # '/' focuses the search box (blur first — the box is auto-focused on load)
    page.click("body")
    page.keyboard.press("/")
    expect(page.locator("#q")).to_be_focused()

    # arrows walk results
    page.fill("#q", "nav")
    page.press("#q", "Enter")
    page.locator("#results a").first.wait_for()
    page.click("body")  # blur the input so arrows act on results
    page.keyboard.press("ArrowDown")
    first = page.evaluate("document.activeElement.textContent")
    assert "Web result for nav" in first
    page.keyboard.press("ArrowDown")
    second = page.evaluate("document.activeElement.textContent")
    assert second == "Second web result"
    page.keyboard.press("ArrowUp")
    back = page.evaluate("document.activeElement.textContent")
    assert "Web result for nav" in back
    assert not errors
    page.close()


def test_answers_tab_markdown(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")
    page.fill("#q", "tallest animal")
    page.press("#q", "Enter")  # reveal tab bar
    page.click('#tabs button[data-tab="answers"]')
    expect(page.locator("#results li")).to_have_count(0)  # no auto-query on tab switch
    page.click("#go")
    expect(page.locator("#results .answer")).to_be_visible()

    # markdown rendered to real elements, markers gone from text
    expect(page.locator(".answer-text .md-h")).to_contain_text("Mock answer for: tallest animal")
    expect(page.locator(".answer-text strong")).to_have_text("bold")
    expect(page.locator(".answer-text em")).to_have_text("shifty")
    expect(page.locator(".answer-text code")).to_have_text("code")
    expect(page.locator(".answer-text ul li")).to_have_count(2)
    link = page.locator(".answer-text a")
    expect(link).to_have_text("details")
    expect(link).to_have_attribute("href", "https://cite.example/3")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    text = page.locator(".answer-text").inner_text()
    assert "**" not in text and "##" not in text
    expect(page.locator(".citations a")).to_have_count(2)  # source footer still rendered
    # raw HTML from upstream must render as literal text, never execute
    assert "<script>bad()</script>" in text
    assert "bad()" not in page.evaluate("() => typeof window.bad")
    assert "MOCK-BRAVE-ANSWERS-KEY" not in page.content()
    assert not errors
    page.close()


def test_places_tab_and_geolocation(browser, app_server):
    errors = []
    ctx = browser.new_context(
        geolocation={"latitude": 37.9838, "longitude": 23.7275},
        permissions=["geolocation"],
    )
    ctx.add_init_script(FAKE_LEAFLET)  # hermetic: never hit the real CDN/tiles
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(e))
    page.goto(app_server + "/")

    # without location: plain places query
    page.fill("#q", "coffee")
    page.press("#q", "Enter")  # reveal tab bar
    page.click('#tabs button[data-tab="places"]')
    page.click("#go")
    expect(page.locator("#results li.place").first).to_contain_text("Pizza place for coffee")
    expect(page.locator("#results li.place").first).not_to_contain_text("near")

    # enable "use my location", reload so fresh state
    page.click("#gear")
    page.check("#places_location")
    page.reload()
    page.fill("#q", "coffee")
    page.click('#tabs button[data-tab="places"]')
    page.click("#go")
    expect(page.locator("#results li.place").first).to_contain_text("near 37.9838,23.7275")
    assert not errors
    ctx.close()


def test_places_loc_override_ui(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")

    # tabs are visible on first load with Web active
    expect(page.locator("#tabs")).to_be_visible()
    expect(page.locator('#tabs button[data-tab="web"]')).to_have_class("active")

    page.fill("#q", "coffee")
    expect(page.locator("#places-opts")).to_be_hidden()

    page.click('#tabs button[data-tab="places"]')
    expect(page.locator("#places-opts")).to_be_visible()
    page.fill("#loc", "Paris, France")
    page.press("#loc", "Enter")  # Enter in the location box runs the Places search
    expect(page.locator("#results li.place").first).to_contain_text(
        "Pizza place for coffee in Paris, France"
    )
    assert not errors
    page.close()


def test_places_preset_location(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")

    # preset disabled/greyed while "use my location" is on
    page.click("#gear")
    page.check("#places_location")
    expect(page.locator("#places_preset")).to_be_disabled()
    page.uncheck("#places_location")
    expect(page.locator("#places_preset")).to_be_enabled()
    page.fill("#places_preset", "Athens, Greece")
    page.click("#close-settings")  # blur -> change event persists the text
    page.reload()

    # preset applies when browser location is off
    page.fill("#q", "coffee")
    page.click('#tabs button[data-tab="places"]')
    page.press("#loc", "Enter")  # empty override -> preset used
    expect(page.locator("#results li.place").first).to_contain_text(
        "Pizza place for coffee in Athens, Greece"
    )

    # tab-level override beats the preset
    page.fill("#loc", "Paris, France")
    page.press("#loc", "Enter")
    expect(page.locator("#results li.place").first).to_contain_text(
        "Pizza place for coffee in Paris, France"
    )
    expect(page.locator("#results li.place").first).not_to_contain_text("Athens")
    assert not errors
    page.close()


def test_tab_toggle_hides_tab(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")
    page.fill("#q", "x")
    page.press("#q", "Enter")
    expect(page.locator('#tabs button[data-tab="news"]')).to_be_visible()

    page.click("#gear")
    page.uncheck("#enabled_news")
    page.reload()
    page.fill("#q", "y")
    page.press("#q", "Enter")
    expect(page.locator('#tabs button[data-tab="news"]')).to_be_hidden()
    expect(page.locator('#tabs button[data-tab="images"]')).to_be_visible()
    assert not errors
    page.close()


def test_settings_flash_and_preset_layout(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")
    page.click("#gear")

    # autosave flash: changing a setting marks its row .flash (green pulse)
    page.select_option("#safesearch", "strict")
    expect(page.locator('#safesearch >> xpath=ancestor::label[1]')).to_have_class(
        re.compile(r"\bflash\b")
    )

    # preset input sits on its own line and is comfortably wide
    box = page.locator("#places_preset").bounding_box()
    row = page.locator("#places_preset >> xpath=ancestor::label[1]").bounding_box()
    assert box["width"] > 240
    assert box["width"] > 0.7 * row["width"]      # spans the row
    assert box["y"] - row["y"] >= 20              # below the label, not beside it

    assert not errors
    page.close()


FAKE_LEAFLET = """
window.__fakeL = { maps: 0, markers: 0, popups: 0, flies: 0 };
const mkObj = () => ({
  addTo(){ return this; }, bindPopup(){ return this; },
  openPopup(){ window.__fakeL.popups++; },
  getLatLng(){ return [37.9, 23.7]; },
  getElement(){ return { classList: { add(){}, remove(){} }, offsetWidth: 0 }; },
});
const mapF = () => ({
  addTo(){ return this; }, flyTo(){ window.__fakeL.flies++; },
  setView(){ return this; }, fitBounds(){}, remove(){}, invalidateSize(){},
});
window.L = new Proxy({}, { get: (t, k) => {
  if (k === 'map')    return () => { window.__fakeL.maps++;    return mapF(); };
  if (k === 'marker') return () => { window.__fakeL.markers++; return mkObj(); };
  if (k === 'latLngBounds') return () => ({});
  return () => ({ addTo(){ return this; } });
}});
"""


def test_images_lightbox(browser, app_server):
    errors = []
    page = new_page(browser, app_server, errors)
    page.goto(app_server + "/")
    page.fill("#q", "fox")
    page.press("#q", "Enter")
    page.click('#tabs button[data-tab="images"]')
    page.press("#q", "Enter")
    expect(page.locator("#results.img-grid img")).to_have_count(1)

    page.click('#results a[data-lightbox="0"]')
    expect(page.locator(".lightbox img")).to_be_visible()
    # lightbox loads the ORIGINAL served by the mock (not the thumb)
    expect(page.locator("#results a[data-lightbox]")).to_have_attribute(
        "href", re.compile(r"fox-fullres\.jpg"))
    src = page.locator(".lightbox img").get_attribute("src") or ""
    assert "fox-fullres.jpg" in src or "picsum" in src  # origin, or blocked-fallback
    expect(page.locator(".lb-bar a").first).to_have_attribute("href", re.compile(r"fox-fullres"))
    expect(page.locator(".lb-bar")).to_contain_text("(1/1)")

    page.keyboard.press("Escape")
    expect(page.locator(".lightbox")).to_be_hidden()
    assert not errors
    page.close()


def test_map_double_opt_in(browser, app_server):
    # Map is default-ON (cog enabled, UI test server allows embeds):
    # covers render, pin-focus-on-click, and user opt-out.
    ctx = browser.new_context()
    ctx.add_init_script(FAKE_LEAFLET)  # stub -> assert wiring without CDN fetch
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(e))
    page.goto(app_server + "/")
    page.fill("#q", "coffee")
    page.press("#q", "Enter")
    page.click('#tabs button[data-tab="places"]')
    page.press("#loc", "Enter")
    expect(page.locator("#results li.place")).to_have_count(2)
    # map links live inside the details popover now — expand the first card
    page.click('#results li.place >> nth=0 >> summary')
    expect(page.locator("a.maplink").first).to_be_visible()
    expect(page.locator("a.site-btn")).to_have_count(1)   # only the result WITH a url
    # the no-url result renders its title as plain text (no dead duplicate-tab link)
    expect(page.locator('#results li.place >> nth=1 >> span.place-title')).to_be_visible()
    expect(page.locator('#results li.place a.title')).to_have_count(0)  # never links
    # blurb above the details flow: description when present, else category
    expect(page.locator(".place-blurb")).to_have_count(2)
    expect(page.locator(".place-blurb").first).to_contain_text("Wood-fired")
    expect(page.locator(".place-blurb").nth(1)).to_contain_text("cafe")
    # photo present on the first card; clicking opens the lightbox
    expect(page.locator("a.place-photo img")).to_have_count(1)
    page.click("a.place-photo")
    expect(page.locator(".lightbox img")).to_be_visible()
    page.keyboard.press("Escape")

    # map defaults ON (server allows): rendered right after the Places search
    expect(page.locator("#map")).to_be_visible()
    assert page.evaluate("window.__fakeL.maps") >= 1
    assert page.evaluate("window.__fakeL.markers") == 2

    # desktop split view: map left, listings right (viewport is 1280 wide)
    mb = page.locator("#map").bounding_box()
    rb = page.locator("#results").bounding_box()
    assert mb["x"] + mb["width"] <= rb["x"] + 1   # strictly left of listings
    assert mb["height"] > 300                      # tall map, not a strip
    assert page.evaluate(
        "document.querySelector('main').classList.contains('places-mode')")

    # clicking a place name flies to and pops its pin (plain-text title: no nav)
    page.click('#results li.place >> nth=1 >> span.place-title')
    assert page.evaluate("window.__fakeL.popups") >= 1
    assert page.evaluate("window.__fakeL.flies") >= 1

    # user opts OUT via cog -> map gone
    page.click("#gear")
    page.uncheck("#map_embed")
    expect(page.locator("#map")).to_be_hidden()
    page.click("#close-settings")

    # opt back in -> map returns
    page.click("#gear")
    page.check("#map_embed")
    expect(page.locator("#map")).to_be_visible()
    page.click("#close-settings")
    assert not errors
    ctx.close()


def test_server_gated_options_are_hidden_not_greyed(browser, app_server):
    # stub the server to say: no map embeds, no answers key
    ctx = browser.new_context()
    ctx.route("**/api/config", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"answers_enabled": false, "map_embed_allowed": false}'))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(e))
    page.goto(app_server + "/")
    expect(page.locator('#tabs button[data-tab="answers"]')).to_be_hidden()
    page.click("#gear")
    for sel in ("#map_embed", "#enabled_answers"):
        expect(page.locator(f"{sel} >> xpath=ancestor::label[1]")).to_be_hidden()
    assert not errors
    ctx.close()


def test_mobile_viewport_responsive(browser, app_server):
    errors = []
    page = browser.new_page(viewport={"width": 375, "height": 667})
    page.on("pageerror", lambda e: errors.append(e))
    page.goto(app_server + "/")
    # phones render at the 75% equivalent (user scale 100 x phone multiplier)
    assert page.evaluate("document.documentElement.style.zoom") == "0.75"
    page.fill("#q", "mobile")
    page.press("#q", "Enter")
    expect(page.locator("#results li").first).to_contain_text("Web result for mobile")
    # search input and Go button stay side by side and on-screen
    box = page.locator("#go").bounding_box()
    assert box["x"] + box["width"] <= 375 + 0.5
    # no horizontal overflow
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1
    assert not errors
    page.close()


def test_ui_scale(page, app_server):
    page.goto(app_server + "/")
    assert page.evaluate("document.documentElement.style.zoom") == "1"  # default
    page.click("#gear")
    page.select_option("#ui_scale", "125")
    assert page.evaluate("document.documentElement.style.zoom") == "1.25"
    page.reload()
    page.click("#gear")
    assert page.evaluate("document.documentElement.style.zoom") == "1.25"
    expect(page.locator("#ui_scale")).to_have_value("125")  # persisted + restored


def test_engine_switch(page, app_server):
    # UI test server has Brave + BrightData keys -> Google/Bing become options
    page.goto(app_server + "/")
    expect(page.locator("#engines")).to_be_visible()
    expect(page.locator("#engines button")).to_have_count(3)
    expect(page.locator('#engines button[data-engine="brave"]'))\
        .to_have_class(re.compile("active"))

    page.click('#engines button[data-engine="google"]')
    page.fill("#q", "hello")
    page.press("#q", "Enter")
    expect(page.locator("#results li").first).to_contain_text("BD result for hello")
    expect(page.locator("#results li")).to_have_count(2)

    # News: Bing drops out (web-only), Google still offered and used
    page.click('#tabs button[data-tab="news"]')
    expect(page.locator("#engines button")).to_have_count(2)
    page.press("#q", "Enter")
    expect(page.locator("#results li").first).to_contain_text("BD news for hello")

    # Places: Brave + Google both serve it -> pills again, BD places render
    page.click('#tabs button[data-tab="places"]')
    expect(page.locator("#engines button")).to_have_count(2)
    page.press("#q", "Enter")
    expect(page.locator("#results li.place").first).to_contain_text("BD place for hello")

    # Images: Google now serves it too -> pills stay, BD images render
    page.click('#tabs button[data-tab="images"]')
    expect(page.locator("#engines button")).to_have_count(2)
    page.press("#q", "Enter")
    expect(page.locator("#results li a[data-lightbox]").first).to_be_visible()

    # choice persists across reload (per user, in localStorage)
    page.reload()
    expect(page.locator('#engines button[data-engine="google"]'))\
        .to_have_class(re.compile("active"))


def test_answers_local_backend_pills(page, app_server):
    # UI server has BRAVE_ANSWERS_KEY + LOCAL_CHAT_BASE_URL -> two backends
    page.goto(app_server + "/")
    page.fill("#q", "tallest animal")
    page.press("#q", "Enter")
    page.click('#tabs button[data-tab="answers"]')
    expect(page.locator("#engines")).to_be_visible()
    expect(page.locator('#engines button')).to_have_count(2)
    expect(page.locator('#engines button[data-engine="brave"]'))\
        .to_have_class(re.compile("active"))
    page.click('#engines button[data-engine="local"]')
    page.click("#go")
    expect(page.locator(".answer-text")).to_contain_text(
        "Local answer for tallest animal: 42.")
    page.reload()
    page.click('#tabs button[data-tab="answers"]')
    expect(page.locator('#engines button[data-engine="local"]'))\
        .to_have_class(re.compile("active"))  # per-user choice persists


def test_deep_link_engine(page, app_server):
    page.goto(app_server + "/?q=hello&tab=web&engine=google")
    # URL engine is honored for this search without overwriting stored prefs
    expect(page.locator("#results li").first).to_contain_text("BD result for hello")
    expect(page.locator('#engines button[data-engine="google"]'))\
        .to_have_class(re.compile("active"))
    page.goto(app_server + "/")
    expect(page.locator('#engines button[data-engine="brave"]'))\
        .to_have_class(re.compile("active"))  # setting never persisted
