#!/usr/bin/env bash
# Stop and remove the go-shortlink container.
set -euo pipefail

CONTAINER="${CONTAINER:-go-shortlink-svc}"

if ! docker container inspect "${CONTAINER}" >/dev/null 2>&1; then
    echo "No container named ${CONTAINER}"
    exit 0
fi

docker stop "${CONTAINER}" >/dev/null
docker rm "${CONTAINER}" >/dev/null
echo "Stopped and removed ${CONTAINER}"
