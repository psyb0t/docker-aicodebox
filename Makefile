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

.PHONY: all build run test test-unit lint format clean help version

all: build ## Build the base image

version: ## Print the version that would be tagged
	@echo $(TAG)

build: ## Build the Docker image, tagged with the pyproject version + :latest
	docker build -t $(IMAGE_NAME):$(TAG) -t $(IMAGE_NAME):latest .

run: build ## Drop into an interactive shell inside the base image
	docker run --rm -it $(IMAGE_NAME):$(TAG) bash

test: test-unit ## Run all tests

test-unit: ## Run the python unit-test suite locally (no docker)
	uv run --group dev pytest -q

lint: ## Lint python sources
	uv run --group dev flake8 aicodebox/
	uv run --group dev pyright aicodebox/ || true

format: ## Format python sources
	uv run --group dev isort aicodebox/
	uv run --group dev black aicodebox/

clean: ## Remove built images and python caches
	docker rmi $(IMAGE_NAME):$(TAG) 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache *.egg-info build dist

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
