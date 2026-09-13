IMAGE_NAME := psyb0t/aicodebox
# Version comes from pyproject.toml so __version__, the wheel metadata, and
# the docker image tag never drift apart. Override at build time via
# `make build VERSION=...` if you need to pin to something other than the
# in-tree value (rare — release flow bumps pyproject + __init__.py + tags
# all in the same commit).
VERSION    ?= $(shell awk -F\" '/^version *= *"/ {print $$2; exit}' pyproject.toml)
TAG        := v$(VERSION)

-include .env
export

.PHONY: all build build-full build-all full-node-lock full-python-lock run test test-unit test-full-image lint format clean help version

all: build ## Build the base image

version: ## Print the version that would be tagged
	@echo $(TAG)

build: ## Build the Docker image, tagged with the pyproject version + :latest
	docker build -t $(IMAGE_NAME):$(TAG) -t $(IMAGE_NAME):latest .

build-full: build ## Build the matching full development-toolchain variant
	docker build \
		-f Dockerfile.full \
		--build-arg BASE_IMAGE=$(IMAGE_NAME):$(TAG) \
		-t $(IMAGE_NAME):$(TAG)-full \
		-t $(IMAGE_NAME):latest-full \
		.

build-all: build build-full ## Build the minimal and full image variants

full-node-lock: ## Regenerate the full-image Node lockfile
	docker run --rm --user "$$(id -u):$$(id -g)" \
		-e HOME=/tmp -e COREPACK_HOME=/tmp/corepack \
		-v "$(CURDIR)/full-node:/work" -w /work \
		node:24.20.0-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e \
		bash -lc 'corepack pnpm@12.3.4 install --lockfile-only --ignore-scripts'

full-python-lock: build ## Regenerate the full-image Python requirements lockfile
	docker run --rm --user "$$(id -u):$$(id -g)" \
		-e UV_CACHE_DIR=/tmp/uv-cache \
		-v "$(CURDIR)/full-python:/work" -w /work \
		--entrypoint uv $(IMAGE_NAME):$(TAG) \
		pip compile --python-version 3.14 \
			--exclude-newer 2026-09-06T19:11:00Z --generate-hashes \
			-o requirements.txt requirements.in

run: build ## Drop into an interactive shell inside the base image
	docker run --rm -it $(IMAGE_NAME):$(TAG) bash

test: test-unit ## Run all tests

test-unit: ## Run the python unit-test suite locally (no docker)
	uv run --group dev pytest -q

test-full-image: build-full ## Build full and verify the documented CLI toolchain
	IMAGE=$(IMAGE_NAME):latest-full bash scripts/test-full-image.sh

lint: ## Lint python sources
	uv run --group dev flake8 aicodebox/
	uv run --group dev pyright aicodebox/ || true

format: ## Format python sources
	uv run --group dev isort aicodebox/
	uv run --group dev black aicodebox/

clean: ## Remove built images and python caches
	docker rmi $(IMAGE_NAME):$(TAG) 2>/dev/null || true
	docker rmi $(IMAGE_NAME):latest 2>/dev/null || true
	docker rmi $(IMAGE_NAME):$(TAG)-full 2>/dev/null || true
	docker rmi $(IMAGE_NAME):latest-full 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache *.egg-info build dist

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
