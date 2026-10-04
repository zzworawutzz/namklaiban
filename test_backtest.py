import sqlite3
from datetime import datetime, timedelta, timezone

import backtest_fast as bt
import core
import ingest as ig

T0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def db(tmp_path):
    c = sqlite3.connect(tmp_path / "b.db")
    c.row_factory = sqlite3.Row
    ig.init_db(c)
    for sid in ("up", "low", "glitch", "slow"):
        c.execute("INSERT INTO stations(id,name,province,lat,lng) VALUES(?,?,?,?,?)", (sid, sid, "P", 14.0, 100.0))

    def add(sid, h, level, pct):
        c.execute("INSERT INTO readings VALUES(?,?,?,?,?)", (sid, core.iso(T0 + timedelta(hours=h)), level, pct, ig.status_of(pct)))
    for h in range(0, 12):
        add("up", h, 1.0 + (0.3 * (h - 4) if h > 4 else 0), 55 + (h - 4) * 3 if h > 4 else 55)   # +0.3 m/h from hour 5
        add("low", h, 1.0 + (0.3 * (h - 4) if h > 4 else 0), 20)                                  # same rise, far below the bank
        add("glitch", h, 1.0 if h < 6 else 9.0, 60)                                                # +8 m in one step
        add("slow", h, 1.0 + 0.05 * h, 60)                                                         # 15 cm in 3 h
    c.commit()
    return c


def test_replay_finds_only_the_real_fast_rise_and_applies_the_cooldown(tmp_path):
    res = bt.replay(db(tmp_path))
    ev = res["events"]
    assert {e["station"] for e in ev} == {"up"}                 # low river, glitch and slow rise stay quiet
    assert len(ev) == 1 and ev[0]["rise"] >= 0.5 and ev[0]["pct"] >= 50
    assert res["glitches"] >= 1 and res["stations"] == 4 and res["span_days"] == 0.5


def test_thresholds_can_be_tuned_and_the_report_reads_well(tmp_path):
    c = db(tmp_path)
    assert bt.replay(c, rise_m=1.5)["events"] == []
    assert {e["station"] for e in bt.replay(c, min_pct=10)["events"]} == {"up", "low"}
    text = bt.report(bt.replay(c), 0.5, 50)
    assert "NEW messages: 1" in text and "only 0.5 days" in text and "up x1" in text


def test_cli_needs_exactly_one_source():
    import pytest
    with pytest.raises(SystemExit):
        bt.main([])
