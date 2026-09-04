#!/usr/bin/env bash
# Stop and remove the go-shortlink container.
set -euo pipefail

CONTAINER="${CONTAINER:-go-shortlink-svc}"
CONTAINER_ENGINE="${CONTAINER_ENGINE:-docker}"

if ! "${CONTAINER_ENGINE}" container inspect "${CONTAINER}" >/dev/null 2>&1; then
    echo "No container named ${CONTAINER}"
    exit 0
fi

"${CONTAINER_ENGINE}" stop "${CONTAINER}" >/dev/null
"${CONTAINER_ENGINE}" rm "${CONTAINER}" >/dev/null
echo "Stopped and removed ${CONTAINER}"
