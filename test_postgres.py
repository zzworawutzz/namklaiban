"""Same behaviour on a real Postgres (embedded via the `pgserver` package, test-only)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

pgserver = pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

import api, core, db, ingest as ig, notify
from conftest import real_rows

AT = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def pg_uri(tmp_path_factory):
    srv = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    yield srv.get_uri()
    srv.cleanup()


@pytest.fixture
def pg(pg_uri, monkeypatch):
    """Fresh empty schema per test, DATABASE_URL pointed at it."""
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    monkeypatch.setenv("DATABASE_URL", pg_uri)
    monkeypatch.setattr(api, "_ready", set())
    monkeypatch.setattr(api, "now", lambda: AT)
    return pg_uri


def test_dsn_detection_and_placeholder_rewrite():
    assert db.is_pg("postgresql://x/y") and db.is_pg("postgres://x") and not db.is_pg("water.db")
    assert db.pg_sql("SELECT 1 WHERE a=? AND b=:name AND c=:name::int") == \
        "SELECT 1 WHERE a=%s AND b=%(name)s AND c=%(name)s::int"


def test_init_is_idempotent_and_coordinates_keep_precision(pg):
    with db.connect() as c:
        ig.init_db(c); ig.init_db(c)
        ig.save(c, real_rows())
        lat = c.execute("SELECT lat FROM stations WHERE id='505018'").fetchone()["lat"]
    assert lat == 14.21513  # DOUBLE PRECISION, not float4 (would be 14.2151298...)


def test_save_counts_and_rerun_idempotent(pg):
    with db.connect() as c:
        ig.init_db(c)
        assert ig.save(c, real_rows()) == (2, 2, 0)
        assert ig.save(c, real_rows()) == (2, 0, 0)
        assert c.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2


def test_prune_run_log_and_locks(pg):
    with db.connect() as c:
        ig.init_db(c)
        ig.save(c, real_rows())
        old = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        c.execute("INSERT INTO readings VALUES('505018',?,1,50,'normal')", (old,))
        assert ig.prune(c, 31) == 1
        assert ig.try_lock(c, "x") is True and ig.try_lock(c, "x") is False
        ig.release_lock(c, "x")
        assert ig.try_lock(c, "x") is True


def test_run_ingest_failure_is_logged_and_connection_recovers(pg):
    with db.connect() as c:
        ig.init_db(c)
        with pytest.raises(ValueError):
            ig.run_ingest(c, loader=lambda: {"data": []})
        assert c.execute("SELECT ok, error FROM ingest_runs").fetchone()["ok"] == 0
        assert ig.run_ingest(c, loader=lambda: {"data": real_rows()})[:3] == (2, 2, 0)


def test_api_end_to_end_on_postgres(pg):
    with db.connect() as c:
        ig.init_db(c); ig.save(c, real_rows()); ig.log_run(c, True, 2, 2, 0)
    cl = TestClient(api.app)
    by = {s["id"]: s for s in cl.get("/stations").json()}
    assert by["505018"]["stale"] is False and by["1098950"]["stale"] is True
    assert len(cl.get("/stations", params={"province": "กาญจน"}).json()) == 1
    assert len(cl.get("/stations", params={"status": "alert"}).json()) == 2
    near = cl.get("/stations/nearby", params={"lat": 14.2, "lng": 99.0, "limit": 2}).json()
    assert [x["id"] for x in near] == ["505018", "1098950"]
    assert len(cl.get("/stations/505018/readings").json()) == 1
    assert cl.get("/stations/nope/readings").status_code == 404
    assert cl.get("/stations/505018/related").status_code == 200
    h = cl.get("/health").json()
    assert h["stations"] == 2 and h["latest_reading"] == "2026-10-01T16:00:00Z"


def test_trend_from_history_on_postgres(pg):
    with db.connect() as c:
        ig.init_db(c); ig.save(c, real_rows())
        for ts, pct in [("2026-10-01T14:00:00Z", 140.0), ("2026-10-01T15:00:00Z", 145.0)]:
            c.execute("INSERT INTO readings VALUES('505018',?,1.0,?,'alert')", (ts, pct))
        row = {r["id"]: r for r in core.latest(c, AT)}["505018"]
    assert row["trend"] == "rising"


def test_notify_with_subscription_on_postgres(pg):
    out = []
    with db.connect() as c:
        ig.init_db(c); ig.save(c, real_rows())
        assert notify.main(["--db", pg, "add", "--channel", "stdout", "--target", "U1",
                            "--lat", "14.2", "--lng", "99.0", "--label", "บ้าน"]) == 0
        assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (1, 0)
        assert notify.run(c, AT, {"stdout": lambda t, m: out.append(m)}) == (0, 0)
    assert "เตือนภัย" in out[0]


def test_cron_endpoint_auth_and_run(pg, monkeypatch):
    cl = TestClient(api.app)
    assert cl.get("/api/cron/ingest").status_code == 401                      # no secret configured
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret")
    assert cl.get("/api/cron/ingest").status_code == 401                      # no header
    assert cl.get("/api/cron/ingest", headers={"Authorization": "Bearer nope"}).status_code == 401
    monkeypatch.setattr(ig, "fetch", lambda: {"data": real_rows()})
    ok = cl.get("/api/cron/ingest", headers={"Authorization": "Bearer s3cret-s3cret-s3cret"})
    assert ok.status_code == 200 and ok.json()["stations"] == 2 and ok.json()["new_readings"] == 2
    again = cl.get("/api/cron/ingest", headers={"Authorization": "Bearer s3cret-s3cret-s3cret"}).json()
    assert again["new_readings"] == 0                                         # idempotent
    with db.connect() as c:                                                   # lock is released afterwards
        assert ig.try_lock(c, "cron") is True
    assert cl.get("/api/cron/ingest", headers={"Authorization": "Bearer s3cret-s3cret-s3cret"}).json() \
        == {"skipped": "another run is in progress"}
