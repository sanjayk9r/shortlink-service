#!/usr/bin/env bash
# Run the go-shortlink container, binding host port 80 -> container 8080.
# The SQLite database lives in a named volume so edits survive restarts.
set -euo pipefail

IMAGE="${IMAGE:-go-shortlink-img}"
TAG="${TAG:-latest}"
CONTAINER="${CONTAINER:-go-shortlink-svc}"
HOST_PORT="${HOST_PORT:-80}"
DATA_VOLUME="${DATA_VOLUME:-go-shortlink-data}"

cd "$(dirname "$0")"

if docker container inspect "${CONTAINER}" >/dev/null 2>&1; then
    echo "Removing existing container ${CONTAINER}..."
    docker rm -f "${CONTAINER}" >/dev/null
fi

docker volume inspect "${DATA_VOLUME}" >/dev/null 2>&1 \
    || docker volume create "${DATA_VOLUME}" >/dev/null

docker run \
    --name "${CONTAINER}" \
    --restart unless-stopped \
    -p "${HOST_PORT}:8080" \
    -v "${DATA_VOLUME}:/data" \
    -d "${IMAGE}:${TAG}"

