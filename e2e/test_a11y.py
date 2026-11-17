# -*- coding: utf-8 -*-
"""What a screen reader, a keyboard or a thumb needs, measured in the real page on a phone: every control has a name, every control is
big enough to hit (WCAG 2.2 "target size", 24 px; the pills and buttons are 36-48 px in practice), no duplicate ids, zoom is not blocked.
Text contrast is in test_contrast.py. Map markers are left out: they are drawn by Leaflet and every station is also in the list."""
import pytest

from conftest import open_page, stub_outside_world

AUDIT = r"""
() => {
  const bad = [];
  const vis = e => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !e.closest('[hidden]'); };
  const named = e => !!((e.getAttribute('aria-label') || '').trim()
    || (e.getAttribute('aria-labelledby') || '').split(' ').some(i => ((document.getElementById(i) || {}).textContent || '').trim())
    || (e.labels && e.labels[0] && e.labels[0].textContent.trim()) || e.textContent.trim() || (e.getAttribute('title') || '').trim());
  const desc = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/)[0] : '');
  document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=tab]').forEach(e => {
    if (!vis(e) || e.closest('.leaflet-marker-icon,.leaflet-marker-pane')) return;
    if (!named(e)) bad.push('no accessible name: ' + desc(e));
    const r = (/^(radio|checkbox)$/.test(e.type) && e.closest('label') || e).getBoundingClientRect();   // a radio or checkbox is hit through its label; a select is not (the label only focuses it)
    if (Math.min(r.width, r.height) < 24) bad.push('target under 24 px: ' + desc(e) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  });
  const seen = {};
  document.querySelectorAll('[id]').forEach(e => { seen[e.id] = (seen[e.id] || 0) + 1; });
  for (const k in seen) if (seen[k] > 1) bad.push('duplicate id: ' + k);
  document.querySelectorAll('img').forEach(i => { if (vis(i) && !i.hasAttribute('alt')) bad.push('img without alt: ' + i.src.slice(-30)); });
  if (/user-scalable\s*=\s*(no|0)|maximum-scale\s*=\s*1(\.0)?\b/.test(document.querySelector('meta[name=viewport]').content)) bad.push('zoom is blocked');
  if (!document.documentElement.lang) bad.push('no lang on <html>');
  if (document.querySelectorAll('h1').length !== 1) bad.push('the page needs exactly one h1');
  return bad;
}
"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_phone_controls_are_named_reachable_and_big_enough(browser, site, scheme):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme, locale="th-TH", is_mobile=True, has_touch=True,
                              service_workers="block")
    stub_outside_world(ctx)
    page = ctx.new_page()
    open_page(page, site, "/pathumthani")
    page.wait_for_selector("#lineCta:not([hidden])", timeout=15000)
    page.wait_for_timeout(1500)
    found = {"province": page.evaluate(AUDIT)}
    page.evaluate("document.querySelector('#resList .row').click()")
    page.wait_for_selector("#detail details.lvl", timeout=15000)
    page.wait_for_timeout(1200)
    found["station card"] = page.evaluate(AUDIT)
    for name, js in (("report dialog", "document.getElementById('btnReport').click()"), ("route dialog", "document.getElementById('btnRoute').click()")):
        page.evaluate(js)
        page.wait_for_timeout(400)
        found[name] = page.evaluate(AUDIT)
    found = {k: v for k, v in found.items() if v}
    assert not found, found
    ctx.close()
