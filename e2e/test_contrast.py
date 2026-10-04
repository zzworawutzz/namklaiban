"""Text contrast (WCAG AA) in light and dark mode, on every screen a visitor reaches. See contrast.py for how it is measured.
A new colour that is too pale for its background fails here, with the element, both colours and the ratio in the message."""
import pytest

import contrast
from conftest import open_page, stub_outside_world, watch


def failing(page):
    return [f"{f['ratio']} (need {f['need']}) {f['fg']} on {f['bg']} {f['px']:.0f}px {f['el']} '{f['text']}'" for f in contrast.audit(page)]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("phone", [True, False], ids=["phone", "desktop"])
def test_every_screen_meets_wcag_aa_contrast(browser, site, scheme, phone):
    size = {"width": 390, "height": 844} if phone else {"width": 1280, "height": 800}
    ctx = browser.new_context(viewport=size, color_scheme=scheme, locale="th-TH", is_mobile=phone, has_touch=phone, service_workers="block",
                              permissions=["geolocation"], geolocation={"latitude": 14.031, "longitude": 100.731})
    stub_outside_world(ctx)
    page = ctx.new_page()
    errors = watch(page)
    found = {}

    def check(name):
        found[name] = failing(page)

    open_page(page, site, "/pathumthani")
    page.wait_for_selector("#lineCta:not([hidden])", timeout=15000)       # the "add the LINE bot" card is part of the page in production
    page.wait_for_timeout(2000)                                  # rain card, shelters etc. settle
    check("province")
    page.evaluate("document.getElementById('btnMe').click()")             # GPS position: nearest-station card with the ground height and nearby gauges
    page.wait_for_selector("#elevBox b", timeout=20000)
    page.wait_for_timeout(1200)
    check("nearest station card with elevation and gauges")
    page.evaluate("document.getElementById('btnClear').click()")
    page.wait_for_timeout(500)
    page.evaluate("document.querySelector('#resList .row').click()")
    page.wait_for_selector("#detail details.lvl", timeout=15000)
    page.wait_for_selector("#shBox .shbox", timeout=15000)
    page.evaluate("document.querySelector('#detail details.lvl summary').click()")
    page.wait_for_timeout(500)
    check("station card (details open)")
    page.evaluate("document.getElementById('btnLayers').click()")
    page.wait_for_timeout(500)
    check("layers panel and legend")
    page.evaluate("document.getElementById('btnLayers').click()")
    for name, js in (("report dialog", "document.getElementById('btnReport').click()"), ("route dialog", "document.getElementById('btnRoute').click()")):
        page.evaluate(js)
        page.wait_for_timeout(500)
        check(name)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    page.fill("#q", "ปทุม")
    page.wait_for_timeout(1500)
    check("search suggestions")
    for name, path in (("help page", "/help.html"), ("privacy page", "/privacy.html"), ("report page", "/report.html?province=ปทุมธานี")):
        page.goto(site + path, wait_until="load")
        page.wait_for_timeout(1500)
        check(name)
    bad = {k: v for k, v in found.items() if v}
    assert not bad, bad
    assert not errors
    ctx.close()
