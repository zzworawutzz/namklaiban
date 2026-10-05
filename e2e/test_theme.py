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


def test_the_quick_button_cycles_auto_light_dark_and_says_which_mode_it_is_in(browser, site):
    ctx, page = make_page(browser, "light")
    errors = watch(page)
    open_page(page, site)
    shown = "[...document.querySelectorAll('#btnTheme g[data-mode]')].filter(g => !g.hidden).map(g => g.dataset.mode)"
    assert page.evaluate(shown) == ["auto"] and "ตามเครื่อง" in page.get_attribute("#btnTheme", "aria-label")
    for want, label in (("light", "สว่าง"), ("dark", "มืด"), ("auto", "ตามเครื่อง")):
        page.click("#btnTheme")
        assert page.evaluate(shown) == [want] and label in page.get_attribute("#btnTheme", "aria-label"), want
        assert page.evaluate("document.querySelector('#themeSeg [aria-pressed=true]').dataset.themeSet") == want    # the panel agrees
    assert page.evaluate("localStorage.getItem('nkb-theme')") is None                                               # back on auto: nothing kept
    page.click("#btnTheme"); page.click("#btnTheme")
    assert bg(page) == DARK_BG and page.evaluate("localStorage.getItem('nkb-theme')") == "dark"
    assert not errors
    ctx.close()


def test_install_button_appears_only_when_the_browser_offers_installing_and_uses_the_saved_prompt_once(browser, site):
    ctx, page = make_page(browser, "light")
    errors = watch(page)
    open_page(page, site)
    assert page.is_hidden("#installBox")                                                       # nothing to install: nothing shown
    page.evaluate("""() => { const e = new Event('beforeinstallprompt', {cancelable: true});
        e.prompt = () => { window.__prompted = (window.__prompted || 0) + 1; return Promise.resolve(); };
        e.userChoice = Promise.resolve({outcome: 'dismissed'}); window.dispatchEvent(e); }""")
    page.evaluate("document.getElementById('btnLayers').click()")
    assert page.is_visible("#btnInstall") and page.is_hidden("#installIos")
    page.click("#btnInstall")
    assert page.evaluate("window.__prompted") == 1 and page.is_hidden("#installBox")           # a saved prompt works once
    assert not errors
    ctx.close()


def test_iphone_gets_the_two_step_instruction_instead_of_a_button(browser, site):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="th-TH", service_workers="block", is_mobile=True, has_touch=True,
                              user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    stub_outside_world(ctx)
    page = ctx.new_page()
    open_page(page, site)
    page.evaluate("document.getElementById('btnLayers').click()")
    assert page.is_visible("#installIos") and page.is_hidden("#btnInstall")
    assert "เพิ่มลงในหน้าจอโฮม" in page.inner_text("#installIos")
    ctx.close()


def test_the_app_manifest_has_png_icons_for_installing(site):
    import json, urllib.request
    m = json.loads(urllib.request.urlopen(site + "/manifest.webmanifest").read())
    sizes = {(i["sizes"], i["type"], i["purpose"]) for i in m["icons"]}
    assert ("192x192", "image/png", "any") in sizes and ("512x512", "image/png", "any") in sizes and ("512x512", "image/png", "maskable") in sizes
    for f in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        r = urllib.request.urlopen(site + "/" + f)
        assert r.status == 200 and r.headers["content-type"] == "image/png" and r.read(8) == b"\x89PNG\r\n\x1a\n", f
