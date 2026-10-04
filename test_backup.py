import json
import os
import stat

import pytest

import backup_export as bk
import db
import floodreports
import ingest as ig


def make(path, subs=()):
    c = db.connect(str(path))
    ig.init_db(c)
    for ch, target, lat, lng, label in subs:
        c.execute("INSERT INTO subscriptions(channel,target,lat,lng,label,digest,notify_level,quiet) VALUES(?,?,?,?,?,?,?,?)",
                  (ch, target, lat, lng, label, 0, "alert", 1))
    c.commit()
    return c


def test_export_then_restore_into_a_fresh_database(tmp_path):
    src = make(tmp_path / "a.db", [("line", "U1", 14.1, 100.2, "บ้าน"), ("line", "C9", 18.8, 98.9, "จ.เชียงใหม่")])
    data = bk.export(src)
    assert [s["target"] for s in data["subscriptions"]] == ["U1", "C9"] and data["flood_reports"] == []
    dst = make(tmp_path / "b.db")
    assert bk.restore(dst, json.loads(json.dumps(data))) == 2
    rows = {r["target"]: dict(r) for r in dst.execute("SELECT * FROM subscriptions")}
    assert rows["U1"]["label"] == "บ้าน" and rows["U1"]["notify_level"] == "alert" and rows["U1"]["quiet"] == 1 and rows["U1"]["digest"] == 0
    assert bk.restore(dst, data) == 0                       # running it twice adds nothing


def test_restore_only_adds_what_is_missing_and_rejects_other_files(tmp_path):
    src = make(tmp_path / "a.db", [("line", "U1", 14.1, 100.2, "บ้าน"), ("line", "U2", 13.7, 100.5, "ที่ทำงาน")])
    data = bk.export(src)
    dst = make(tmp_path / "b.db", [("line", "U1", 14.1, 100.2, "บ้าน")])
    assert bk.restore(dst, data) == 1
    assert dst.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 2
    with pytest.raises(ValueError):
        bk.restore(dst, {"hello": "world"})


def test_cli_writes_a_private_file(tmp_path, capsys):
    make(tmp_path / "a.db", [("line", "U1", 14.1, 100.2, "บ้าน")])
    out = tmp_path / "out.json"
    assert bk.main(["--db", str(tmp_path / "a.db"), "--out", str(out)]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert json.loads(out.read_text(encoding="utf-8"))["subscriptions"][0]["target"] == "U1"
    assert "U1" not in capsys.readouterr().out               # ids are never printed to the screen
    fresh = tmp_path / "b.db"
    assert bk.main(["--db", str(fresh), "--restore", str(out)]) == 0
    assert db.connect(str(fresh)).execute("SELECT target FROM subscriptions").fetchone()["target"] == "U1"
