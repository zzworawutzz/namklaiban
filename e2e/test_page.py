"""What a visitor sees and does. Each test also fails if the page throws an uncaught error or breaks the CSP (see conftest)."""
import re

from conftest import open_page, stub_outside_world, until, watch


def test_map_summary_and_one_line_load(page, site):
    open_page(page, site)
    assert page.inner_text("#sumTitle") == "จ.ปทุมธานี"
    one = page.inner_text("#oneLine")
    assert one.startswith("วันนี้ จ.ปทุมธานี:") and "เตือนภัย 1 สถานี" in one and "น้ำขึ้นเร็ว 1 แห่ง" in one
    until(page, "document.getElementById('oneLine').innerText.includes('ฝนตกมาแล้ว')")   # rain joins the line
    until(page, "getComputedStyle(document.getElementById('emergency')).display !== 'none'", 6)   # the emergency numbers come back after boot


def test_stations_are_requested_once_and_early(context, site):
    seen = []
    page = context.new_page()
    page.on("request", lambda r: seen.append(r.url) if re.search(r"/stations(\?|$)", r.url) else None)
    errors = watch(page)
    open_page(page, site)
    assert len(seen) == 1, seen
    assert not errors


def test_province_list_sorts_by_level_by_fast_rise_and_near_is_off_without_a_place(page, site):
    open_page(page, site)
    rows = lambda: page.eval_on_selector_all("#resList .row", "els => els.map(e => e.innerText.split('\\n')[0])")
    assert rows() == ["ท่าช้าง", "บางบาล", "คลองหลวง"][:len(rows())] or rows()[0] == "ท่าช้าง"      # highest % first
    page.click("#resSort button[data-sort=fast]")
    assert page.inner_text("#resTitle").startswith("น้ำขึ้นเร็วที่สุดใน จ.ปทุมธานี")
    assert rows()[0] == "ท่าช้าง" and "ซม. ใน 3 ชม." in page.inner_text("#resList .row")
    assert page.is_disabled("#resSort button[data-sort=near]")
    assert page.get_attribute("#resSort button[data-sort=fast]", "aria-pressed") == "true"


def test_alert_station_shows_nearest_shelter_with_a_working_phone_link(page, site):
    open_page(page, site, "/?station=A")
    page.wait_for_selector("#shBox .shbox", timeout=15000)
    box = page.inner_text("#shBox")
    assert "ศูนย์พักพิงที่ใกล้สถานีนี้ที่สุด" in box and "กม." in box
    tel = page.get_attribute("#shBox a[href^=tel]", "href")
    assert re.fullmatch(r"tel:\+?\d{6,}", tel), tel                       # digits only: "0 2577 1964" must not become "tel:0"


def test_normal_station_has_no_shelter_box(page, site):
    open_page(page, site, "/?station=C")
    page.wait_for_selector("#detail h2")
    page.wait_for_timeout(1500)
    assert page.inner_text("#shBox").strip() == ""


def test_compare_two_stations_overlays_two_lines_and_closes(page, site):
    open_page(page, site, "/?station=A")
    page.wait_for_selector(".cmpbtn", timeout=15000)
    page.click(".cmpbtn >> nth=0")
    page.wait_for_selector(".cmp svg")
    assert page.locator(".cmp svg path").count() == 2
    assert "เทียบกับ" in page.inner_text(".cmp h2")
    page.click("#cmpClose")
    assert page.locator(".cmp").count() == 0


def test_area_outline_is_navy_in_light_mode_and_lighter_blue_in_dark_mode(browser, site):
    for scheme, colour in (("light", "#1e3a8a"), ("dark", "#5b8cff")):
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme=scheme, service_workers="block")
        stub_outside_world(ctx)
        page = ctx.new_page()
        errors = watch(page)
        open_page(page, site)
        page.wait_for_selector(".leaflet-overlay-pane path", state="attached", timeout=15000)
        assert page.get_attribute(".leaflet-overlay-pane path", "stroke") == colour, scheme
        assert not errors
        ctx.close()


def test_low_battery_switches_off_radar_and_satellite_once(context, site):
    context.add_init_script("""(() => { const l = {}; const b = {level: 0.6, charging: false,
      addEventListener(t, f){ (l[t] = l[t] || []).push(f); }}; window.__b = b; window.__fire = t => (l[t]||[]).forEach(f => f());
      navigator.getBattery = () => Promise.resolve(b); })();""")
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    page.evaluate("document.getElementById('chkRadar').click()")          # (the layers panel is closed, so click it directly)
    assert page.is_checked("#chkRadar")
    page.evaluate("window.__b.level = 0.5; window.__fire('levelchange')")
    assert page.is_checked("#chkRadar")                                    # 50%: left alone
    page.evaluate("window.__b.level = 0.15; window.__fire('levelchange')")
    until(page, "!document.getElementById('chkRadar').checked", 5)
    assert "แบตเตอรี่เหลือ 15%" in page.inner_text("#toast")
    page.evaluate("document.getElementById('chkRadar').click()")           # the visitor may turn it back on
    page.evaluate("window.__fire('levelchange')")
    assert page.is_checked("#chkRadar")
    assert not errors


def test_emergency_numbers_appear_even_if_the_map_libraries_never_load(context, site):
    context.route("**/cdnjs.cloudflare.com/**", lambda r: r.abort())
    page = context.new_page()
    page.goto(site + "/", wait_until="domcontentloaded")
    until(page, "getComputedStyle(document.getElementById('emergency')).display !== 'none'", 6)
    assert "1784" in page.inner_text("#emergency")


def test_saved_stations_are_shown_with_their_age_while_the_network_is_slow_then_replaced(context, site):
    first = context.new_page()
    open_page(first, site)                                                  # a normal visit saves the list on this device
    first.wait_for_timeout(500)
    first.close()
    page = context.new_page()
    errors = watch(page)
    gate = {}

    def hold(route):                                                        # the network answer for /stations is held back
        gate["route"] = route
    page.route("**/stations", hold)
    page.goto(site + "/pathumthani", wait_until="domcontentloaded")
    page.wait_for_selector("#cacheBar:not([hidden])", timeout=15000)
    text = page.inner_text("#cacheBar")
    assert "กำลังอัปเดต" in text and "บันทึกเมื่อ" in text and "ก่อน)" in text
    page.wait_for_selector(".leaflet-marker-icon")                          # the map already shows the saved stations
    gate["route"].continue_()                                               # the real answer arrives
    page.wait_for_selector("#cacheBar", state="hidden", timeout=15000)
    assert not errors


def test_offline_with_saved_stations_keeps_the_map_and_says_so(context, site):
    first = context.new_page()
    open_page(first, site)
    first.wait_for_timeout(500)
    first.close()
    page = context.new_page()
    errors = watch(page)
    page.route("**/stations", lambda r: r.abort())
    page.goto(site + "/pathumthani", wait_until="domcontentloaded")
    page.wait_for_selector("#cacheBar:not([hidden])", timeout=15000)
    until(page, "document.getElementById('cacheBar').innerText.includes('เชื่อมต่อไม่ได้')")
    assert "1784" in page.inner_text("#cacheBar")
    assert page.locator(".leaflet-marker-icon").count() >= 1
    assert page.is_hidden("#err")
    assert not errors


def test_first_ever_visit_with_a_dead_api_shows_the_error_bar(context, site):
    page = context.new_page()
    page.route("**/stations", lambda r: r.abort())
    page.goto(site + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#err:not([hidden])", timeout=15000)
    assert page.is_hidden("#cacheBar")
