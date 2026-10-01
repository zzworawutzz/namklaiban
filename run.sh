#!/bin/sh
# One-container runner: ingest + notify every INGEST_EVERY seconds in the background,
# the API (and web UI) in the foreground.
INTERVAL="${INGEST_EVERY:-1200}"
(
  while true; do
    python ingest.py --db "$WATER_DB" ${THRESHOLDS_FILE:+--thresholds "$THRESHOLDS_FILE"} \
      && python notify.py --db "$WATER_DB" run
    sleep "$INTERVAL"
  done
) &
exec uvicorn api:app --host 0.0.0.0 --port "${PORT:-8000}"
