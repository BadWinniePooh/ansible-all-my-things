#!/bin/sh
# Entrypoint for the self-contained web frontend image.
#
# Runs the first-run/version-skew seeding step, then starts the server.
# Seeding never overwrites anything on its own (webui/seed.py:
# ensure_seeded() only writes marker files when they are missing); an
# actual defaults refresh happens only via POST /defaults/refresh, at the
# user's request.
set -eu

python -m webui.seed

exec python -m uvicorn webui.app:app \
    --host "${WEBUI_HOST:-0.0.0.0}" \
    --port "${WEBUI_PORT:-8080}"
