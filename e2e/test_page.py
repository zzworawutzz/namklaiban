"""What a visitor sees and does. Each test also fails if the page throws an uncaught error or breaks the CSP (see conftest)."""
import re

from conftest import open_page, stub_outside_world, until, watch


def test_map_summary_pills_and_one_line_load_and_agree(page, site):
    open_page(page, site)
    assert page.inner_text("#sumTitle") == "จ.ปทุมธานี"
    pills = page.inner_text("#sumPills")
    verdict = page.inner_text("#sumBody .vh")
    assert "เตือนภัย 1" in pills, pills                                            # visible in the pulled-down sheet too
    assert re.search(r"เตือนภัย.* 1 จาก 3 สถานี", verdict), verdict               # same number as the status card
    one = page.inner_text("#oneLine")
    assert "น้ำขึ้นเร็ว 1 แห่ง" in one and "เตือนภัย" not in one                    # the line adds news, it does not repeat the card
    until(page, "document.getElementById('oneLine').innerText.includes('ฝนตกมาแล้ว')")   # rain joins the line
    until(page, "getComputedStyle(document.getElementById('emergency')).display !== 'none'", 6)   # the emergency numbers come back after boot


def test_legend_explains_that_a_dark_circle_is_a_group_with_a_count(page, site):
    open_page(page, site)
    page.evaluate("document.getElementById('btnLayers').click()")
    legend = page.inner_text(".lg")
    assert "กลุ่มสถานี" in legend and "จำนวนสถานี" in legend and "ไม่ใช่ %" in legend


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


def test_station_card_keeps_technical_numbers_in_a_closed_details_block(page, site):
    open_page(page, site, "/?station=A")
    page.wait_for_selector("#detail details.lvl")
    assert page.get_attribute("#detail details.lvl", "open") is None
    assert "รทก." not in page.inner_text("#detail .big, #detail .head")           # the headline stays plain: % of the bank
    page.click("#detail details.lvl summary")
    assert "ท้องน้ำ" in page.inner_text("#detail details.lvl") and "ม.รทก." in page.inner_text("#detail details.lvl")


def test_phone_buttons_are_big_enough_and_chart_text_is_readable(browser, site):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                              locale="th-TH", service_workers="block")
    stub_outside_world(ctx)
    page = ctx.new_page()
    errors = watch(page)
    open_page(page, site, "/?station=A")
    page.wait_for_selector("#detail details.lvl")
    small = page.evaluate("""() => [...document.querySelectorAll('a[href],button,summary,select')].filter(e => {
        const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && !e.closest('.leaflet-control, .leaflet-marker-icon, #ar, #rt')
               && e.id !== 'prov' && e.id !== 'dist' && e.id !== 'tam' && (r.height < 43.5 || r.width < 43.5)
      }).map(e => e.tagName + '#' + e.id + '.' + String(e.className).slice(0, 20) + ' ' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height))""")
    assert not small, small                                                         # every control meets the 44 px touch-target size
    texts = page.evaluate("[...document.querySelectorAll('svg text')].map(t => parseFloat(t.getAttribute('font-size')))")
    assert texts and min(texts) >= 11, texts                                        # chart labels are no smaller than 11 px
    assert not errors
    ctx.close()


def test_selected_marker_is_not_hidden_behind_the_floating_buttons(browser, site):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="th-TH", service_workers="block")
    stub_outside_world(ctx)
    page = ctx.new_page()
    errors = watch(page)
    open_page(page, site)
    page.evaluate("document.querySelector('#resList .row').click()")               # pick the first station in the list
    page.wait_for_selector(".mk.sel", timeout=15000)
    page.wait_for_timeout(2500)                                                      # let the map finish flying
    marker = page.eval_on_selector(".mk.sel", "e => { const r = e.getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; }")
    fab = page.eval_on_selector("#fab", "e => e.getBoundingClientRect().top")
    sheet = page.eval_on_selector("#sheet", "e => e.getBoundingClientRect().top")
    assert marker["bottom"] <= min(fab, sheet) + 2, (marker, fab, sheet)
    assert not errors
    ctx.close()


def test_a_tampered_map_library_is_refused_by_the_browser_and_the_emergency_numbers_still_show(context, site):
    def tamper(route):                                           # what a compromised CDN would do: same URL, different bytes
        r = route.fetch()
        route.fulfill(response=r, body=r.body() + b"\nwindow.__pwned = true;")
    context.route("**/cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js", tamper)
    page = context.new_page()
    page.goto(site + "/", wait_until="domcontentloaded")
    until(page, "getComputedStyle(document.getElementById('emergency')).display !== 'none'", 6)
    assert page.evaluate("typeof L") == "undefined"              # the library did not run
    assert page.evaluate("window.__pwned === true") is False     # and neither did the injected code
    assert "1784" in page.inner_text("#emergency")


def test_rain_card_shows_what_the_gauges_measured_next_to_the_forecast(page, site):
    open_page(page, site)
    page.wait_for_selector("#rainCard:not([hidden])", timeout=15000)
    until(page, "document.getElementById('gaugeLine').innerText.includes('สูงสุด 62 มม.')")
    box = page.inner_text("#gaugeLine")
    assert "รร.วัดทดสอบ" in box and "11 สถานี" in box and "35 มม." in box and "ThaiWater" in box
    # a CSS class shared with the bank-level bar once made this text overlap itself: the text must flow normally
    assert page.eval_on_selector("#gaugeLine b", "e => getComputedStyle(e).position") == "static"
    assert page.eval_on_selector("#gaugeLine", "e => getComputedStyle(e).display") == "block"


def test_gps_near_an_alert_station_shows_the_ground_height_but_never_compares_it_with_the_water(context, site):
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": 14.031, "longitude": 100.731})              # next to station A, which is at alert level
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    page.evaluate("document.getElementById('btnMe').click()")
    page.wait_for_selector("#elevBox b", timeout=20000)
    elev = page.inner_text("#elevBox")
    assert "≈ 2.6 ม.รทก." in elev
    assert not any(w in elev for w in ("สูงกว่า", "ต่ำกว่า", "ใกล้เคียง", "ระดับน้ำที่สถานี")), elev   # no "you are above the water" next to an alert
    assert "ระดับเตือนภัย" in elev and "ไม่นำมาเทียบกับระดับน้ำ" in elev and "1784" in elev
    assert "ไม่ได้บอกว่าจะท่วมหรือไม่ท่วม" in elev
    until(page, "document.getElementById('gaugeLine') && document.getElementById('gaugeLine').innerText.includes('ปตร.ทดสอบ')")
    assert "2.4 กม." in page.inner_text("#gaugeLine")
    assert not errors


def test_gps_near_a_normal_station_compares_the_ground_height_with_its_water_level(context, site):
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": 14.201, "longitude": 100.501})              # next to station C (normal, water about 0.3 m)
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    page.evaluate("document.getElementById('btnMe').click()")
    page.wait_for_selector("#elevBox b", timeout=20000)
    elev = page.inner_text("#elevBox")
    assert "คลองหลวง" in elev and "สูงกว่าระดับน้ำที่สถานี" in elev, elev
    assert "ไม่ได้บอกว่าจะท่วมหรือไม่ท่วม" in elev
    assert not errors


def test_gps_near_a_normal_station_that_is_over_halfway_up_the_bank_shows_only_the_ground_height(context, site):
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": 14.601, "longitude": 100.901})              # next to station E: normal, but 60 % of the bank
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    page.evaluate("document.getElementById('btnMe').click()")
    page.wait_for_selector("#elevBox b", timeout=20000)
    elev = page.inner_text("#elevBox")
    assert "≈ 2.6 ม.รทก." in elev and "60% ของตลิ่ง" in elev and "ไม่นำมาเทียบกับระดับน้ำ" in elev, elev
    assert not any(w in elev for w in ("สูงกว่า", "ต่ำกว่า", "ใกล้เคียง", "ระดับน้ำที่สถานี")), elev
    assert not errors
