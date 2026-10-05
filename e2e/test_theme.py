"""Light / dark colour mode: follows the device by default, can be forced either way, is remembered, and stays readable."""
import pytest

import contrast
from conftest import open_page, stub_outside_world, watch

DARK_BG, LIGHT_BG = "rgb(10, 23, 29)", "rgb(243, 246, 248)"


def make_page(browser, scheme):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme, locale="th-TH", service_workers="block")
    stub_outside_world(ctx)
    return ctx, ctx.new_page()


def bg(page):
    return page.evaluate("getComputedStyle(document.body).backgroundColor")


def choose(page, value):
    page.evaluate("document.getElementById('btnLayers').click()")
    page.evaluate(f"document.querySelector('#themeSeg [data-theme-set={value}]').click()")


def test_the_choice_overrides_the_device_in_both_directions_and_auto_goes_back(browser, site):
    for device, want_choice, want_bg in (("light", "dark", DARK_BG), ("dark", "light", LIGHT_BG)):
        ctx, page = make_page(browser, device)
        errors = watch(page)
        open_page(page, site)
        assert bg(page) == (DARK_BG if device == "dark" else LIGHT_BG)                       # no choice yet: the device decides
        assert page.evaluate("document.querySelector('#themeSeg [aria-pressed=true]').dataset.themeSet") == "auto"
        choose(page, want_choice)
        assert bg(page) == want_bg and page.evaluate("document.documentElement.dataset.theme") == want_choice
        assert page.evaluate("localStorage.getItem('nkb-theme')") == want_choice
        page.reload(wait_until="domcontentloaded")                                           # remembered, and applied before the page is drawn
        assert page.evaluate("document.documentElement.dataset.theme") == want_choice and bg(page) == want_bg
        page.wait_for_selector("#sumBody .verdict", timeout=30000)
        choose(page, "auto")
        assert page.evaluate("document.documentElement.dataset.theme") is None and page.evaluate("localStorage.getItem('nkb-theme')") is None
        assert bg(page) == (DARK_BG if device == "dark" else LIGHT_BG)
        assert not errors
        ctx.close()


def test_the_browser_colour_follows_the_choice(browser, site):
    ctx, page = make_page(browser, "light")
    open_page(page, site)
    choose(page, "dark")
    assert page.evaluate("[...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content)") == ["#0a171d", "#0a171d"]
    choose(page, "auto")
    assert page.evaluate("[...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content)") == ["#f3f6f8", "#0a171d"]   # the originals, per device mode
    ctx.close()


@pytest.mark.parametrize("device,choice", [("light", "dark"), ("dark", "light")])
def test_a_forced_mode_is_readable_on_the_map_page_and_the_layers_panel(browser, site, device, choice):
    ctx, page = make_page(browser, device)
    open_page(page, site, "/pathumthani")
    page.wait_for_timeout(1500)
    choose(page, choice)
    page.wait_for_timeout(500)
    bad = contrast.audit(page)
    assert not bad, [f"{f['ratio']} (need {f['need']}) {f['fg']} on {f['bg']} {f['el']} '{f['text']}'" for f in bad]
    ctx.close()


@pytest.mark.parametrize("path", ["/help.html", "/privacy.html", "/report.html?province=ปทุมธานี"])
def test_the_other_pages_use_the_saved_choice(browser, site, path):
    ctx, page = make_page(browser, "light")
    page.add_init_script("try{localStorage.setItem('nkb-theme','dark')}catch(e){}")
    page.goto(site + path, wait_until="load")
    page.wait_for_timeout(800)
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert bg(page) == DARK_BG, (path, bg(page))
    bad = contrast.audit(page)
    assert not bad, [f"{f['ratio']} (need {f['need']}) {f['el']} '{f['text']}'" for f in bad]
    ctx.close()
