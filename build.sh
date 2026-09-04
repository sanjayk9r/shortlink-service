#!/usr/bin/env bash

# Build the go-shortlink Docker image.
set -euo pipefail

IMAGE="${IMAGE:-go-shortlink-img}"
TAG="${TAG:-latest}"
CONTAINER_ENGINE="${CONTAINER_ENGINE:-docker}"

cd "$(dirname "$0")"

echo "Building ${IMAGE}:${TAG} with ${CONTAINER_ENGINE}..."
"${CONTAINER_ENGINE}" build -t "${IMAGE}:${TAG}" .
echo "Build successful: ${IMAGE}:${TAG}"
