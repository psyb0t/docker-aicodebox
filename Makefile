IMAGE_NAME := psyb0t/aicodebox
TAG        := latest

-include .env
export

.PHONY: all build run test test-unit lint format clean help

all: build ## Build the base image

build: ## Build the Docker image
	docker build -t $(IMAGE_NAME):$(TAG) .

run: build ## Drop into an interactive shell inside the base image
	docker run --rm -it $(IMAGE_NAME):$(TAG) bash

test: test-unit ## Run all tests

test-unit: ## Run the python unit-test suite locally (no docker)
	python -m pip install --quiet -e ".[test]"
	python -m pytest -q

lint: ## Lint python sources
	python -m flake8 aicodebox/
	python -m pyright aicodebox/ || true

format: ## Format python sources
	python -m isort aicodebox/
	python -m black aicodebox/

clean: ## Remove built images and python caches
	docker rmi $(IMAGE_NAME):$(TAG) 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache *.egg-info build dist

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
