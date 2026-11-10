IMAGE_NAME := psyb0t/aicodebox
# Version comes from pyproject.toml so __version__, the wheel metadata, and
# the docker image tag never drift apart. Override at build time via
# `make build VERSION=...` if you need to pin to something other than the
# in-tree value (rare; the release flow bumps pyproject.toml and tags
# all in the same commit).
VERSION    ?= $(shell awk -F\" '/^version *= *"/ {print $$2; exit}' pyproject.toml)
TAG        := v$(VERSION)

-include .env
export

# Lint and the CI test suite run in this pinned uv image, so they need only
# Docker. The virtual environment lives inside the container, never in the
# checkout, and no host .env value reaches it.
DEV_IMAGE := ghcr.io/astral-sh/uv:0.13.0-python3.14-trixie-slim@sha256:8e70ac2e85d09a5bb7ef09753b80671a60a71c8d24f195e4890d71e03865e36d
DEV_RUN = docker run --rm \
	--user "$$(id -u):$$(id -g)" \
	-e HOME=/tmp \
	-e UV_CACHE_DIR=/tmp/uv-cache \
	-e UV_PROJECT_ENVIRONMENT=/tmp/venv \
	-e UV_PYTHON_DOWNLOADS=never \
	-v "$(CURDIR):/work" \
	-w /work \
	$(DEV_IMAGE)

DOCS_SITE := .testing/docs-site

.PHONY: all build build-full build-all pkg-lock full-go-lock full-node-lock full-python-lock run test test-unit test-ci test-integration test-entrypoint-identity test-full-image lint docs clean help version

all: build ## Build the base image

V ?=
export V
version: ## Print or update the package version without committing: make version V=X.Y.Z
	@set -eu; \
	if [ -z "$$V" ]; then printf '%s\n' "$(TAG)"; exit 0; fi; \
	printf '%s\n' "$$V" | LC_ALL=C grep -qE '^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.]+)?$$' || { echo "V must be semver X.Y.Z" >&2; exit 1; }; \
	uv version --no-sync "$$V"

build: ## Build the Docker image, tagged with the pyproject version + :latest
	docker build -t $(IMAGE_NAME):$(TAG) -t $(IMAGE_NAME):latest .

build-full: build ## Build the matching full development-toolchain variant
	docker build \
		-f Dockerfile.full \
		--build-arg BASE_IMAGE=$(IMAGE_NAME):latest \
		-t $(IMAGE_NAME):$(TAG)-full \
		-t $(IMAGE_NAME):latest-full \
		.

build-all: build build-full ## Build the minimal and full image variants

pkg-lock: ## Refresh the Python dependency lock under the configured age cutoff
	uv lock

full-go-lock: ## Refresh full-image Go tool checksums
	docker run --rm --user "$$(id -u):$$(id -g)" \
		-e HOME=/tmp -e GOPATH=/tmp/go -e GOCACHE=/tmp/go-build \
		-v "$(CURDIR)/full-go:/work" -w /work \
		golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61 \
		sh -c 'go mod download all && cd gopls && go mod download all'

full-node-lock: ## Regenerate the full-image Node lockfile
	docker run --rm --user "$$(id -u):$$(id -g)" \
		-e HOME=/tmp -e COREPACK_HOME=/tmp/corepack \
		-v "$(CURDIR)/full-node:/work" -w /work \
		node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 \
		bash -lc 'corepack pnpm@12.12.1 update --depth Infinity --lockfile-only --ignore-scripts'

full-python-lock: build ## Regenerate the full-image Python requirements lockfile
	docker run --rm --user "$$(id -u):$$(id -g)" \
		-e UV_CACHE_DIR=/tmp/uv-cache \
		-v "$(CURDIR)/full-python:/work" -w /work \
		--entrypoint uv $(IMAGE_NAME):$(TAG) \
		pip compile --python-version 3.14 \
			--exclude-newer 2026-10-12T00:00:00Z --generate-hashes \
			-o requirements.txt requirements.in

# The base image ships no agent, so pass bash as the agent binary. The
# entrypoint still runs its startup steps and drops to the aicode user.
run: build ## Drop into an interactive shell inside the base image
	docker run --rm -it -e AICODEBOX_AGENT_BINARY=bash $(IMAGE_NAME):$(TAG)

test: test-unit ## Run all tests

test-unit: ## Run the python unit-test suite locally (no docker)
	uv run --group dev pytest -q

# The API modules read the workspace root at import time, before any fixture
# runs, so point it at a directory the unprivileged container user can create.
test-ci: ## Run the python unit-test suite in the pinned uv container (what CI runs)
	$(DEV_RUN) sh -c 'AICODEBOX_WORKSPACE=/tmp/workspace uv run --frozen --group dev pytest -q'

test-integration: build ## Build and verify startup hooks, ownership and socket permissions
	IMAGE=$(IMAGE_NAME):$(TAG) bash scripts/test-entrypoint-hooks.sh
	IMAGE=$(IMAGE_NAME):$(TAG) bash scripts/test-entrypoint-identity.sh

test-entrypoint-identity: ## Test workspace and socket identity remapping with an existing image
	bash scripts/test-entrypoint-identity.sh

test-full-image: build-full ## Build full and verify the documented CLI toolchain
	IMAGE=$(IMAGE_NAME):latest-full bash scripts/test-full-image.sh

lint: ## Lint python sources with flake8 and pyright in the pinned uv container
	$(DEV_RUN) sh -c '\
		uv run --frozen --group dev --group lint flake8 aicodebox/ && \
		uv run --frozen --group dev --group lint pyright aicodebox/'

# Builds docs/ with the image GitHub Pages uses, so the local result matches
# the published site. The output directory is gitignored.
docs: ## Build the documentation site into .testing/docs-site
	@mkdir -p "$(DOCS_SITE)"
	docker run --rm \
		--user "$$(id -u):$$(id -g)" \
		-e HOME=/tmp \
		-e GITHUB_WORKSPACE=/work \
		-e GITHUB_REPOSITORY=psyb0t/docker-aicodebox \
		-e INPUT_SOURCE=docs \
		-e INPUT_DESTINATION="$(DOCS_SITE)" \
		-e INPUT_VERBOSE=false \
		-v "$(CURDIR):/work" \
		ghcr.io/actions/jekyll-build-pages:v1.0.13@sha256:6791ebfd912185ed59bfb5fb102664fa872496b79f87ff8b9cfba292a7345041

clean: ## Remove built images and python caches
	docker rmi $(IMAGE_NAME):$(TAG) 2>/dev/null || true
	docker rmi $(IMAGE_NAME):latest 2>/dev/null || true
	docker rmi $(IMAGE_NAME):$(TAG)-full 2>/dev/null || true
	docker rmi $(IMAGE_NAME):latest-full 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache *.egg-info build dist

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
