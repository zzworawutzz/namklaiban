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


def test_province_list_sorts_by_level_and_by_fast_rise(page, site):
    open_page(page, site)
    rows = lambda: page.eval_on_selector_all("#resList .row", "els => els.map(e => e.innerText.split('\\n')[0])")
    assert rows() == ["ท่าช้าง", "บางบาล", "คลองหลวง"][:len(rows())] or rows()[0] == "ท่าช้าง"      # highest % first
    page.click("#resSort button[data-sort=fast]")
    assert page.inner_text("#resTitle").startswith("น้ำขึ้นเร็วที่สุดใน จ.ปทุมธานี")
    assert rows()[0] == "ท่าช้าง" and "ซม. ใน 3 ชม." in page.inner_text("#resList .row")
    assert not page.is_disabled("#resSort button[data-sort=near]")                 # never a dead button
    assert page.get_attribute("#resSort button[data-sort=fast]", "aria-pressed") == "true"
    page.click("#resSort button[data-sort=over]")
    assert page.inner_text("#resTitle").startswith("ล้นตลิ่งมากที่สุดใน จ.ปทุมธานี")
    assert rows()[0] == "ท่าช้าง"                                                   # the only station at the bank comes first, the ones far below follow by %
    assert page.get_attribute("#resSort button[data-sort=over]", "aria-pressed") == "true"


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
        until(page, "Array.from(document.querySelectorAll('.leaflet-overlay-pane path')).some(p => p.getAttribute('stroke') === '%s')" % colour, 15)   # the district shapes are in the same pane, so look for the outline by its colour
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
    assert "ฝนสะสม 3 วัน (2–4 ต.ค.) สูงสุด 188 มม." in box and "ปตร.ฝนมาก" in box and "ตั้งแต่ 100 มม. ขึ้นไป 2 สถานี" in box and "ไม่รวมวันนี้" in box, box
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
    near = page.inner_text("#gaugeLine")
    assert "2.4 กม." in near and "3 วัน 77 มม." in near and "ฝนสะสม 2–4 ต.ค." in near and "ไม่รวมวันนี้" in near, near      # the gauge without a 3-day figure just shows its 24 h
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


def test_a_shared_point_link_opens_the_stations_around_that_point(page, site):
    errors = watch(page)
    page.goto(site + "/?lat=14.0312&lng=100.7311", wait_until="domcontentloaded")          # extra digits are rounded to about 100 m
    page.wait_for_selector("#nearCard:not([hidden]) .nearbtn", timeout=30000)
    card = page.inner_text("#nearCard")
    assert "จุดที่แชร์" in card and "ท่าช้าง" in card, card                                   # the nearest station to that point is A
    page.wait_for_selector("#elevBox b", timeout=20000)                                    # the ground height is shown like for a pinned home
    assert not errors


def test_bad_shared_point_links_are_ignored(page, site):
    errors = watch(page)
    for q in ("?lat=99&lng=10", "?lat=abc&lng=100", "?lat=14.0", "?lat=14&lng=1e9"):
        page.goto(site + "/" + q, wait_until="domcontentloaded")
        page.wait_for_selector("#sumBody .verdict", timeout=30000)
        assert page.is_hidden("#nearCard"), q
    assert not errors


def test_share_button_gives_a_link_with_only_the_rounded_point(context, site):
    context.add_init_script("window.__shared = []; navigator.share = function(d){ window.__shared.push(d.url); return Promise.resolve(); };")
    page = context.new_page()
    errors = watch(page)
    page.goto(site + "/?lat=14.0312&lng=100.7311&station=A", wait_until="domcontentloaded")
    page.wait_for_selector("#sumBody .verdict", timeout=30000)
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": 14.201234, "longitude": 100.501234})              # this visitor's own position
    page.evaluate("document.getElementById('btnMe').click()")
    page.wait_for_selector("#btnSharePt", timeout=20000)
    page.click("#btnSharePt")
    until(page, "window.__shared.length === 1")
    url = page.evaluate("window.__shared[0]")
    assert url == site + "/?lat=14.201&lng=100.501", url                                  # 3 decimals, no station, nothing else
    assert not errors


def test_rain_card_adds_up_the_last_three_days_and_says_so_when_it_has_been_a_lot(page, site):
    errors = watch(page)
    open_page(page, site)
    page.wait_for_selector("#rainCard:not([hidden])", timeout=15000)
    note = page.inner_text("#rainCard")
    assert "3 วันรวม ~120 มม." in note and "ฝนสะสม 3 วันค่อนข้างมาก" in note and "ประมาณจากแบบจำลอง" in note, note    # 12 readings of 10 mm in the last 72 h
    assert not errors


def test_dam_card_lists_the_big_dams_of_the_basin_with_the_caveat(page, site):
    errors = watch(page)
    open_page(page, site)
    page.wait_for_selector("#damCard:not([hidden])", timeout=15000)
    text = page.inner_text("#damCard")
    assert "เขื่อนทดสอบหนึ่ง" in text and "110%" in text and "ระบายออก 43 ล้าน ลบ.ม./วัน" in text and "เขื่อนทดสอบสอง" in text and "61%" in text, text
    assert "▲ +13.3 จุดใน 7 วัน" in text and "▼ -2.4 จุดใน 7 วัน" in text and "+0.3" not in text and "▲ +0" not in text, text   # a week's change, only when it is at least a point
    assert "และอีก 3 แห่ง" in text and "ไม่ได้บอกว่าจะท่วมหรือไม่ท่วม" in text and "1784" in text
    assert not errors


def test_forecast_days_start_today_and_are_all_labelled_even_though_the_answer_also_lists_past_days(page, site):
    errors = watch(page)
    open_page(page, site)
    page.wait_for_selector("#rainCard:not([hidden])", timeout=15000)
    days = page.eval_on_selector_all("#rainCard .day", "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())")
    assert len(days) == 3, days                                                          # exactly today, tomorrow, the day after
    assert [d.split(" ")[0] for d in days] == ["วันนี้", "พรุ่งนี้", "มะรืนนี้"], days
    assert "undefined" not in page.inner_text("#rainCard")
    assert days[0].startswith("วันนี้ 6 มม.") and days[1].startswith("พรุ่งนี้ 4 มม.") and days[2].startswith("มะรืนนี้ 2 มม."), days   # the past days (31-33 mm) never appear as forecast
    assert not errors


def test_share_as_picture_makes_a_1080_by_1350_png_and_hands_it_to_the_share_sheet(context, site):
    context.add_init_script("window.__files = []; navigator.canShare = d => !!d.files; navigator.share = d => { window.__files.push({file: d.files[0], text: d.text}); return Promise.resolve(); };")
    page = context.new_page()
    errors = watch(page)
    page.goto(site + "/pathumthani?station=A", wait_until="domcontentloaded")
    page.wait_for_selector("#btnShareImg", timeout=30000)
    page.click("#btnShareImg")
    until(page, "window.__files.length === 1", timeout=20)
    info = page.evaluate("""async () => { const {file, text} = window.__files[0]; const bmp = await createImageBitmap(file);
        return {name: file.name, type: file.type, size: file.size, w: bmp.width, h: bmp.height, text}; }""")
    assert info["name"] == "namklaiban-A.png" and info["type"] == "image/png" and info["w"] == 1080 and info["h"] == 1350 and info["size"] > 20000, info
    assert "ท่าช้าง" in info["text"] and "ของตลิ่ง" in info["text"] and "?station=A" in info["text"], info["text"]
    assert page.is_enabled("#btnShareImg")                                                   # usable again afterwards
    assert not errors


def test_share_as_picture_downloads_the_file_when_the_browser_has_no_share_sheet(context, site):
    context.add_init_script("for (const k of ['share', 'canShare']) Object.defineProperty(Navigator.prototype, k, {value: undefined, configurable: true});")
    page = context.new_page()
    errors = watch(page)
    page.goto(site + "/pathumthani?station=A", wait_until="domcontentloaded")
    page.wait_for_selector("#btnShareImg", timeout=30000)
    with page.expect_download(timeout=20000) as dl:
        page.click("#btnShareImg")
    assert dl.value.suggested_filename == "namklaiban-A.png"
    until(page, "document.getElementById('shareMsg').innerText.includes('บันทึกภาพแล้ว')")
    assert not errors


def test_gps_puts_a_pulsing_dot_on_the_map_and_clearing_removes_it(context, site):
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": 14.031, "longitude": 100.731})
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    assert page.locator(".mepos").count() == 0
    page.evaluate("document.getElementById('btnMe').click()")
    page.wait_for_selector(".mepos .mering", state="attached", timeout=20000)
    assert page.evaluate("getComputedStyle(document.querySelector('.mepos .mering')).animationName") == "mepulse"
    assert page.locator(".mepos .medot").count() == 1
    page.evaluate("document.getElementById('btnClear').click()")
    until(page, "document.querySelectorAll('.mepos').length === 0")
    assert not errors


def test_follow_moves_the_dot_with_the_user_and_stops_when_switched_off(context, site):
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": 14.031, "longitude": 100.731})
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    page.evaluate("var c=document.getElementById('chkFollow'); c.checked=true; c.dispatchEvent(new Event('change'))")
    page.wait_for_selector(".mepos .medot", state="attached", timeout=20000)
    before = page.evaluate("document.querySelector('.mepos').getBoundingClientRect().left")
    context.set_geolocation({"latitude": 14.0315, "longitude": 100.7345})            # about 400 m east: the dot moves, no new lookup needed
    until(page, "Math.abs(document.querySelector('.mepos').getBoundingClientRect().left - %s) > 3" % before, 20)
    page.evaluate("window.__cleared = 0; var o = navigator.geolocation.clearWatch.bind(navigator.geolocation); navigator.geolocation.clearWatch = function(i){ window.__cleared++; return o(i); }")
    page.evaluate("var c=document.getElementById('chkFollow'); c.checked=false; c.dispatchEvent(new Event('change'))")
    assert page.evaluate("window.__cleared") == 1                                      # switched off: the position watch is released
    page.evaluate("document.getElementById('btnClear').click()")
    until(page, "document.querySelectorAll('.mepos').length === 0")
    assert not errors


def test_near_me_tab_asks_for_the_position_and_then_sorts_by_distance(context, site):
    context.grant_permissions(["geolocation"])
    context.set_geolocation({"latitude": 14.201, "longitude": 100.501})              # next to station C, the farthest from A
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    page.click("#resSort button[data-sort=near]")
    until(page, "document.getElementById('resTitle').innerText.startsWith('ใกล้คุณที่สุดใน จ.ปทุมธานี')", 20)
    assert page.evaluate("document.querySelector('#resList .row').innerText.split('\\n')[0]") == "คลองหลวง"
    assert page.get_attribute("#resSort button[data-sort=near]", "aria-pressed") == "true"
    assert not errors


def test_station_card_says_how_far_above_the_bank_and_only_when_it_is_known(page, site):
    open_page(page, site, "/?station=A")
    page.wait_for_selector(".overbank", timeout=15000)
    txt = page.inner_text(".overbank")
    assert ("ตลิ่งราว" in txt or "เสมอระดับตลิ่ง" in txt) and "ไม่ใช่ความลึกน้ำที่บ้านคุณ" in txt     # A is at the bank in the test data
    page.goto(site + "/?station=B", wait_until="domcontentloaded")
    page.wait_for_selector("#sumBody .verdict", timeout=30000)
    page.wait_for_selector(".big", timeout=15000)
    assert page.locator(".overbank").count() == 0                                                     # B is far below the bank: nothing to say


STATION_D_JS = """(() => { const m = document.querySelector('.leaflet-marker-icon[title^="เมืองนนท์"]'), c = document.getElementById('map').getBoundingClientRect();
    if (!m) return null; const r = m.getBoundingClientRect(); return {dx: (r.left + r.width / 2) - (c.left + c.width / 2), dy: (r.top + r.height / 2) - (c.top + c.height / 2)}; })()"""


def assert_fitted_in_the_uncovered_area(page):
    """A fit leaves the point in the middle of the part of the map the sheet and top bar do not cover: on a 1280 px desktop
    that is 208 px right of and 36 px below the middle of the whole map (the paddings in fitPad())."""
    box = page.evaluate(STATION_D_JS)
    assert box is not None, "station D is not on the map"
    assert abs(box["dx"] - 208) < 40 and abs(box["dy"] - 36) < 40, box


def test_map_survives_losing_its_size_and_fits_when_it_comes_back(page, site):
    open_page(page, site)
    page.evaluate("document.getElementById('map').style.display = 'none'")             # e.g. a hidden tab or a layout that collapses the map
    page.wait_for_timeout(300)
    page.evaluate("var p = document.getElementById('prov'); p.value = 'นนทบุรี'; p.dispatchEvent(new Event('change'))")   # asks the map to fit that province
    page.wait_for_timeout(300)
    page.evaluate("document.getElementById('map').style.display = ''")
    page.wait_for_timeout(1000)
    assert "NaN" not in page.evaluate("document.querySelector('.leaflet-map-pane').style.transform")
    assert_fitted_in_the_uncovered_area(page)


def test_map_that_starts_with_no_size_fits_once_it_has_one(context, site):
    context.add_init_script("""new MutationObserver((_, o) => { if (document.head) { const s = document.createElement('style'); s.id = 'nosize';
        s.textContent = '#map{display:none !important}'; document.head.appendChild(s); o.disconnect(); } }).observe(document, {childList: true, subtree: true});""")   # in place before app.js builds the map
    page = context.new_page()
    errors = watch(page)
    page.goto(site + "/nonthaburi", wait_until="domcontentloaded")
    page.wait_for_selector("#sumBody .verdict", timeout=30000)                          # the data arrives while the map has no size, so the fit has to wait
    page.wait_for_timeout(500)
    page.evaluate("document.getElementById('nosize').remove()")
    page.wait_for_selector(".leaflet-marker-icon", timeout=15000)
    page.wait_for_timeout(1200)
    assert "NaN" not in page.evaluate("document.querySelector('.leaflet-map-pane').style.transform")
    assert_fitted_in_the_uncovered_area(page)
    assert not errors


def test_cm_over_the_bank_is_in_the_list_the_marker_title_and_the_chart_axis(page, site):
    open_page(page, site)                                                                  # /pathumthani: A is at the bank, B and C far below it
    first = page.inner_text("#resList .row")
    assert first.startswith("ท่าช้าง") and "เสมอระดับตลิ่ง" in first, first               # the unit of the day: cm next to the %, in the list
    assert "ตลิ่งราว" not in page.inner_text("#resList") and page.locator("#resList .row").count() >= 2   # stations far below the bank say nothing
    assert page.evaluate("Array.from(document.querySelectorAll('.leaflet-marker-icon')).some(m => (m.title || '').includes('เสมอระดับตลิ่ง'))")
    page.goto(site + "/?station=A", wait_until="domcontentloaded")
    page.wait_for_selector("svg.chart", timeout=20000)
    labels = page.evaluate("Array.from(document.querySelectorAll('svg.chart text')).map(t => t.textContent)")
    assert "ซม.ตลิ่ง" in labels and "0" in labels and any(l.startswith("-") for l in labels), labels
    assert "แกนขวาเป็นเซนติเมตร" in page.get_attribute("svg.chart", "aria-label")


def test_the_csp_blocks_an_injected_inline_script_and_an_inline_handler_but_not_our_own_scripts(context, site):
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)                                                                   # our own inline script (theme) and app.js ran: the map is up
    page.evaluate("var s = document.createElement('script'); s.textContent = 'window.__pwned = 1'; document.head.appendChild(s)")
    page.evaluate("var b = document.createElement('button'); b.id = 'evil'; b.setAttribute('onclick', 'window.__pwned2 = 1'); document.body.appendChild(b); b.click()")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned") is None and page.evaluate("window.__pwned2") is None
    assert sum("Content Security Policy" in e for e in errors) >= 2, errors                 # the browser reported both attempts
    errors.clear()


def _health(page, **fields):
    import json
    body = json.dumps({"stations": 800, "stale": 5, "ingest_ok": True, "last_ingest_age_min": 5, "latest_reading_age_min": 10, "quiet_agencies": [], **fields})
    page.route("**/health", lambda r: r.fulfill(status=200, content_type="application/json", body=body))


def test_a_notice_names_an_agency_whose_stations_have_all_gone_quiet_while_the_rest_report(page, site):
    _health(page, quiet_agencies=["FOP", "HII"])
    open_page(page, site)
    page.wait_for_selector("#delayBar:not([hidden])", timeout=15000)
    text = page.inner_text("#delayBar")
    assert "FOP, HII" in text and "ต้นทาง ThaiWater ไม่ใช่ระบบของเรา" in text and "หน่วยงานอื่นยังปกติ" in text and "1784" in text, text


def test_no_notice_when_every_agency_reports(page, site):
    _health(page)
    open_page(page, site)
    page.wait_for_timeout(1500)
    assert page.is_hidden("#delayBar")


def test_report_page_shows_the_share_of_stations_by_status_as_a_ring_with_whole_percentages(page, site):
    page.goto(site + "/report.html?province=ปทุมธานี", wait_until="domcontentloaded")
    page.wait_for_selector(".ring svg", timeout=15000)
    label = page.get_attribute(".ring svg", "aria-label")
    assert label.startswith("สัดส่วนสถานีตามสถานะ"), label
    rows = page.eval_on_selector_all(".ring li", "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())")
    pcts = [int(r.split("%")[0].split()[-1]) for r in rows if "%" in r]
    assert pcts and sum(pcts) == 100, rows                                     # the rounded shares always add up to exactly 100
    assert page.locator(".ring svg text").first.text_content().strip().isdigit()                  # the total sits in the middle
    assert page.locator(".ring circle[stroke-dasharray]").count() >= 1


_FAKE_SPEECH = """
(function(){
  var voices = %s, spoken = [], speaking = false;
  window.__spoken = spoken;
  var ss = {getVoices: function(){ return voices; }, speak: function(u){ speaking = true; spoken.push(u.text); window.__utt = u; }, cancel: function(){ speaking = false; window.__cancelled = (window.__cancelled||0)+1; },
            get speaking(){ return speaking; }, addEventListener: function(){}};
  Object.defineProperty(window, "speechSynthesis", {value: ss, configurable: true});
  window.SpeechSynthesisUtterance = function(t){ this.text = t; };
})();
"""


def test_read_aloud_button_speaks_the_summary_in_words_and_stops_on_a_second_press(context, site):
    page = context.new_page()
    page.add_init_script(_FAKE_SPEECH % '[{lang: "th-TH", name: "Kanya"}]')
    errors = watch(page)
    open_page(page, site)
    page.wait_for_selector("#btnSpeak:not([hidden])", timeout=15000)
    page.click("#btnSpeak")
    spoken = page.evaluate("window.__spoken")
    assert len(spoken) == 1, spoken
    t = spoken[0]
    assert "เปอร์เซ็นต์" in t and "จังหวัด" in t and "%" not in t and "▲" not in t and "·" not in t and "จ." not in t, t   # numbers and symbols turned into words
    assert "% ของตลิ่ง = ระดับน้ำ" not in t and "ระดับน้ำเทียบกับความสูงตลิ่ง" not in t                              # the small-print legend is not read
    assert page.evaluate("window.__utt.lang") == "th-TH" and page.get_attribute("#btnSpeak", "aria-pressed") == "true"
    page.click("#btnSpeak")                                                                                         # while it speaks, the same button stops it
    assert page.evaluate("window.__cancelled") >= 1 and page.get_attribute("#btnSpeak", "aria-pressed") == "false"
    assert not errors


def test_read_aloud_button_stays_hidden_without_a_thai_voice(context, site):
    page = context.new_page()
    page.add_init_script(_FAKE_SPEECH % '[{lang: "en-US", name: "Samantha"}]')
    errors = watch(page)
    open_page(page, site)
    page.wait_for_timeout(500)
    assert page.is_hidden("#btnSpeak")
    assert not errors


def _english(context):
    page = context.new_page()
    page.add_init_script("try{localStorage.setItem('nkb-lang','en')}catch(e){}")
    return page


def test_english_menu_translates_the_screen_and_the_dynamic_parts_too(context, site):
    page = _english(context)
    errors = watch(page)
    open_page(page, site)
    assert page.evaluate("document.documentElement.lang") == "en"
    body = page.inner_text("body")
    for want in ("Report flooding", "Check a route", "Water situation", "Normal"):
        assert want in body, want
    assert "User guide" in page.text_content("#foot") and "Privacy policy" in page.text_content("#foot")
    assert page.text_content("#emergency h2") == "Emergency numbers (free)"
    pills = page.inner_text("#sumPills")
    assert "Alert" in pills or "Watch" in pills or "Normal" in pills, pills
    rows = page.inner_text("#sheetBody")
    assert "ทั้งประเทศ" not in body and "All of Thailand" in body and "ปกติ 9%" not in rows and "Normal 9%" in rows, rows        # the chips and the % badges too, not just the static page
    assert "▲ rising" in rows and "Pathum Thani" in rows                                                                         # the list lines "province · trend"
    assert "Pathum Thani" in page.inner_text("#sumTitle"), page.inner_text("#sumTitle")                      # the province name, not "จ.ปทุมธานี"
    assert page.get_attribute("#q", "placeholder") == "Search stations, places", page.get_attribute("#q", "placeholder")
    page.click("#btnLayers")
    layers = page.inner_text("#layers")
    assert "Map layers" in page.get_attribute("#btnLayers", "aria-label") and page.inner_text("#btnLayers").strip() == "Layers"
    assert "Rain radar" in layers and "Temporary shelters (DDPM)" in layers and "Colour mode" in layers
    assert page.inner_text("#langSeg") == "ไทยEnglish" or "English" in page.inner_text("#langSeg")           # the language buttons stay as they are in both languages
    assert page.get_attribute("#langSeg button[data-lang-set=en]", "aria-pressed") == "true"
    assert not errors


def test_thai_stays_the_default_and_the_language_buttons_switch_and_remember(context, site):
    page = context.new_page()
    errors = watch(page)
    open_page(page, site)
    assert page.evaluate("document.documentElement.lang") != "en" and "แจ้งจุดน้ำท่วม" in page.inner_text("body")
    page.click("#btnLayers")
    assert page.get_attribute("#langSeg button[data-lang-set=th]", "aria-pressed") == "true"
    page.click("#langSeg button[data-lang-set=en]")                                   # reloads the page in English
    page.wait_for_selector("#sumBody .verdict", timeout=30000)
    assert page.evaluate("localStorage.getItem('nkb-lang')") == "en" and "Report flooding" in page.inner_text("body")
    page.click("#btnLayers")
    page.click("#langSeg button[data-lang-set=th]")                                   # and back
    page.wait_for_selector("#sumBody .verdict", timeout=30000)
    assert page.evaluate("localStorage.getItem('nkb-lang')") is None and "แจ้งจุดน้ำท่วม" in page.inner_text("body")
    assert not errors
