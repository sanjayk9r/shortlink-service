#!/usr/bin/env bash
# Run the go-shortlink container, binding host port 80 -> container 8080.
# The SQLite database lives in a named volume so edits survive restarts.
set -euo pipefail

IMAGE="${IMAGE:-go-shortlink-img}"
TAG="${TAG:-latest}"
CONTAINER="${CONTAINER:-go-shortlink-svc}"
HOST_PORT="${HOST_PORT:-80}"
# HOST_BIND="${HOST_BIND:-127.0.0.1}"
DATA_VOLUME="${DATA_VOLUME:-go-shortlink-data}"
CONTAINER_ENGINE="${CONTAINER_ENGINE:-docker}"

cd "$(dirname "$0")"

if "${CONTAINER_ENGINE}" container inspect "${CONTAINER}" >/dev/null 2>&1; then
    echo "Removing existing container ${CONTAINER}..."
    "${CONTAINER_ENGINE}" rm -f "${CONTAINER}" >/dev/null
fi

"${CONTAINER_ENGINE}" volume inspect "${DATA_VOLUME}" >/dev/null 2>&1 \
    || "${CONTAINER_ENGINE}" volume create "${DATA_VOLUME}" >/dev/null

"${CONTAINER_ENGINE}" run \
    --name "${CONTAINER}" \
    --restart unless-stopped \
    -p "${HOST_PORT}:8080" \
    -v "${DATA_VOLUME}:/data" \
    -d "${IMAGE}:${TAG}"
