import copy

import pytest

# Trimmed from a real waterlevel_load response (2026-10-01).
REAL_ROWS = [
  {"id": 1316004504, "waterlevel_datetime": "2026-10-01 23:00", "waterlevel_m": None,
   "waterlevel_msl": "50.27", "storage_percent": "148.91",
   "agency": {"agency_shortname": {"th": "ชป.", "en": "RID"}},
   "station": {"id": 505018, "tele_station_name": {"th": "บ้านปากแซง"}, "tele_station_lat": 14.21513,
               "tele_station_long": 99.058197, "tele_station_oldcode": "K.58", "min_bank": 45.8, "ground_level": 36.66},
   "geocode": {"province_name": {"th": "กาญจนบุรี", "en": "Kanchanaburi"}}},
  {"id": 1316314164, "waterlevel_datetime": "2026-10-01 13:00", "waterlevel_m": None,
   "waterlevel_msl": "24.00", "storage_percent": "135.33",
   "agency": {"agency_shortname": {"th": "ชป.", "en": "RID"}},
   "station": {"id": 1098950, "tele_station_name": {"th": "บ้านแก้ง"}, "tele_station_lat": 13.93635,
               "tele_station_long": 101.972321, "tele_station_oldcode": "Kgt.12A", "min_bank": 21, "ground_level": 12.509},
   "geocode": {"province_name": {"th": "ปราจีนบุรี", "en": "Prachin Buri"}}},
]

def real_rows():
    return copy.deepcopy(REAL_ROWS)


@pytest.fixture(autouse=True)
def _no_ambient_database(monkeypatch):
    """Tests use SQLite unless they opt in; never touch a DATABASE_URL from the shell."""
    for k in ("DATABASE_URL", "POSTGRES_URL", "CRON_SECRET", "VERCEL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _fresh_rate_limiter(monkeypatch):
    import ratelimit
    monkeypatch.delenv("RATE_LIMIT_PER_MIN", raising=False)
    ratelimit.limiter.reset()


@pytest.fixture(autouse=True)
def _no_real_rain_forecast(monkeypatch):
    """Tests never call Open-Meteo: dry forecast by default, and a fresh cache per test."""
    import rainalert
    rainalert._cache.clear()
    monkeypatch.setattr(rainalert, "fetch", lambda lat, lng: {"hourly": {"precipitation": [0.0] * 24}})
