#!/usr/bin/env bash
# Run the Flask app locally (no Docker) using the built-in dev server.
# Intended for development; use Docker + gunicorn for anything else.
set -euo pipefail

export FLASK_APP="${FLASK_APP:-app.py}"
export FLASK_DEBUG="${FLASK_DEBUG:-1}"
export FLASK_RUN_HOST="${FLASK_RUN_HOST:-127.0.0.1}"
export FLASK_RUN_PORT="${FLASK_RUN_PORT:-8080}"

cd "$(dirname "$0")"
exec flask run
