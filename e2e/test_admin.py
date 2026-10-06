"""The owner's admin page (/admin.html): needs the cron secret, shows quota / messages / database / silent stations,
keeps the secret only in the tab, and is readable in light and dark mode."""
import pytest

import contrast
from conftest import watch

SECRET = "e2e-secret"   # set as CRON_SECRET for the test server in conftest.py


def login(page, site, secret):
    page.goto(site + "/admin.html", wait_until="load")
    page.fill("#secret", secret)
    page.click("#f button[type=submit]")


def test_wrong_secret_is_refused_and_nothing_is_kept(browser, site):
    ctx = browser.new_context(service_workers="block")
    page = ctx.new_page()
    login(page, site, "not-the-secret")
    page.wait_for_selector("#loginErr:not([hidden])", timeout=10000)
    assert "รหัสไม่ถูกต้อง" in page.inner_text("#loginErr")
    assert page.is_hidden("#dash")
    assert page.evaluate("sessionStorage.getItem('nkb-admin')") is None
    ctx.close()


def test_right_secret_shows_the_dashboard_and_logout_forgets_it(browser, site):
    ctx = browser.new_context(service_workers="block")
    page = ctx.new_page()
    errors = watch(page)
    login(page, site, SECRET)
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)
    text = page.inner_text("#dash")
    assert "โควตาข้อความ LINE" in text and "ถามโควตาจาก LINE ไม่ได้" in text            # the test server has no LINE bot: shown honestly, not hidden
    assert "ฐานข้อมูล" in text and "MB" in text and "ปทุมธานี" in text                 # database size and WATCH_PROVINCES
    assert "ไม่มีกลุ่มสถานีที่เงียบ" in text or "สถานีที่เงียบ" in text
    assert page.is_hidden("#login")
    assert page.evaluate("localStorage.length") == 0                                  # never on disk
    page.reload(wait_until="load")                                                    # same tab: still signed in
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)
    page.click("#logout")
    assert page.is_visible("#login") and page.evaluate("sessionStorage.getItem('nkb-admin')") is None
    assert not errors
    ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_admin_page_meets_contrast_in_both_modes(browser, site, scheme):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme, service_workers="block")
    page = ctx.new_page()
    page.goto(site + "/admin.html", wait_until="load")
    bad = contrast.audit(page)
    login(page, site, SECRET)
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)
    page.wait_for_timeout(500)
    bad += contrast.audit(page)
    assert not bad, [f"{f['ratio']} (need {f['need']}) {f['el']} '{f['text']}'" for f in bad]
    ctx.close()


FULL = {"days": [{"day": "2026-10-05", "total": 14, "failed": 2, "by_kind": {"digest": 1, "admin": 3, "status": 10}},
                 {"day": "2026-10-04", "total": 1, "failed": 0, "by_kind": {"digest": 1}}],
        "top_provinces": [{"province": "พระนครศรีอยุธยา", "messages": 12}], "subscriptions": 3, "db_mb": 450.5, "db_limit_mb": 512.0,
        "silent_groups": ["RID จ.กาญจนบุรี (11 สถานี)"], "watch_provinces": [], "backup": {"since": "2026-09-05T01:00:00Z", "days_left": 2},
        "line_quota": {"limit": 300, "used": 292}, "budget": {"reserve": 15, "holding": True},
        "bank_data": {"fresh": 700, "usable": 90, "far_below": 600, "missing": 4, "too_high_count": 2, "disagree_count": 1, "bank_not_above_bed_count": 1,
                      "too_high": [{"id": "x1", "name": "สถานีสูงผิดปกติ", "province": "ตาก", "cm": 415, "pct": 130}, {"id": "x2", "name": "สถานีสูงสอง", "province": "ตาก", "cm": 310, "pct": None}],
                      "disagree": [{"id": "x3", "name": "สถานีขัดกัน", "province": "ลำพูน", "cm": 40, "pct": 80}],
                      "bank_not_above_bed": [{"id": "x4", "name": "สถานีตลิ่งต่ำ", "province": "น่าน", "bank": 3.0, "bed": 3.5}]}}


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_dashboard_with_a_nearly_full_quota_says_so_and_stays_readable(browser, site, scheme):
    import json
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme, service_workers="block")
    ctx.route("**/api/cron/stats*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(FULL)))
    page = ctx.new_page()
    login(page, site, SECRET)
    page.wait_for_selector("#dash:not([hidden])", timeout=10000)
    text = page.inner_text("#dash")
    assert "292" in text and "/ 300" in text and "เหลือ 8 ข้อความ" in text and "กำลังพักข้อความไม่เร่งด่วน" in text
    assert "RID จ.กาญจนบุรี (11 สถานี)" in text and "ถึงเจ้าของ" in text and "ทั้งประเทศ" in text
    assert "อีก 2 วัน" in text
    assert "คุณภาพข้อมูลตลิ่ง" in text and "4 สถานีข้อมูลตลิ่งน่าสงสัย" in text and "จาก 700 สถานีที่ข้อมูลสด" in text
    assert "สถานีสูงผิดปกติ" in text and "415 ซม. · 130%" in text and "สถานีขัดกัน" in text and "ตลิ่ง 3 · ท้องน้ำ 3.5" in text and "รวม 2" in text
    bad = contrast.audit(page)
    assert not bad, [f"{f['ratio']} (need {f['need']}) {f['el']} '{f['text']}'" for f in bad]
    ctx.close()
