.PHONY: help install dev test lint run docker-build docker-run docker-stop docker-logs clean

IMAGE ?= go-shortlink-img
TAG ?= latest
CONTAINER ?= go-shortlink-svc
HOST_BIND ?= 127.0.0.1
HOST_PORT ?= 80
CONTAINER_ENGINE ?= docker

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install runtime dependencies
	pip install -r requirements.txt

dev:  ## Install dev dependencies (runtime + pytest)
	pip install -r requirements-dev.txt

test:  ## Run the test suite
	pytest -q

run:  ## Run the dev server locally on 127.0.0.1:8080
	./shortlink-start.sh

docker-build:  ## Build the Docker image
	IMAGE=$(IMAGE) TAG=$(TAG) CONTAINER_ENGINE=$(CONTAINER_ENGINE) ./build.sh

docker-run:  ## Run the container (host port 80 -> container 8080)
	IMAGE=$(IMAGE) TAG=$(TAG) CONTAINER=$(CONTAINER) \
	HOST_BIND=$(HOST_BIND) HOST_PORT=$(HOST_PORT) CONTAINER_ENGINE=$(CONTAINER_ENGINE) ./run.sh

docker-stop:  ## Stop and remove the container
	CONTAINER=$(CONTAINER) CONTAINER_ENGINE=$(CONTAINER_ENGINE) ./stop.sh

docker-logs:  ## Tail container logs
	$(CONTAINER_ENGINE) logs -f $(CONTAINER)

clean:  ## Remove caches and build artefacts
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache
