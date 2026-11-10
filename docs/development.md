---
title: "Development"
description: "Work on docker-aicodebox itself: repository layout, Make targets, the containerized lint and unit tests, entrypoint integration tests, CI and the docs build."
nav_order: 10
---

# Development

You need Docker and Make. `make help` lists every target. Lint and the CI test suite run inside a pinned uv container, so they need nothing else on the host.

## Layout

| Path | Contents |
| --- | --- |
| [`aicodebox/adapters/`](https://github.com/psyb0t/docker-aicodebox/tree/main/aicodebox/adapters) | The adapter contract, `AgentAdapter`, and the JSON helpers. |
| [`aicodebox/shared/`](https://github.com/psyb0t/docker-aicodebox/tree/main/aicodebox/shared) | The runner (sync, streaming, schema retries), model and effort lists, the harness JSON parser, logging. |
| [`aicodebox/modes/`](https://github.com/psyb0t/docker-aicodebox/tree/main/aicodebox/modes) | One package per mode: `api` (with the OpenAI and MCP code), `telegram`, `cron`, `mcp`. |
| [`aicodebox/tests/`](https://github.com/psyb0t/docker-aicodebox/tree/main/aicodebox/tests) | Unit tests, with a stub adapter in `conftest.py`. Not shipped in the wheel. |
| [`entrypoint.sh`](https://github.com/psyb0t/docker-aicodebox/blob/main/entrypoint.sh) | Container startup and mode dispatch. |
| [`Dockerfile`](https://github.com/psyb0t/docker-aicodebox/blob/main/Dockerfile), [`Dockerfile.full`](https://github.com/psyb0t/docker-aicodebox/blob/main/Dockerfile.full) | The minimal and full images. |
| [`full-go/`](https://github.com/psyb0t/docker-aicodebox/tree/main/full-go), [`full-node/`](https://github.com/psyb0t/docker-aicodebox/tree/main/full-node), [`full-python/`](https://github.com/psyb0t/docker-aicodebox/tree/main/full-python) | Locked tool sets for the full image. |
| [`scripts/`](https://github.com/psyb0t/docker-aicodebox/tree/main/scripts) | Integration tests that run real containers. |
| `docs/` | This site. |

## Commands

| Target | Does |
| --- | --- |
| `make build` | Build `psyb0t/aicodebox:v<version>` and `:latest`. The version comes from [`pyproject.toml`](https://github.com/psyb0t/docker-aicodebox/blob/main/pyproject.toml). |
| `make build-full` | Build the minimal image, then `:v<version>-full` and `:latest-full` on top of it. |
| `make build-all` | Both variants. |
| `make lint` | flake8 and pyright over `aicodebox/`, in the uv container. |
| `make test` | The unit tests on the host with your own uv (same as `make test-unit`). |
| `make test-ci` | The unit tests in the uv container. This is what CI runs. |
| `make test-integration` | Build the image, then run the startup hook and identity tests against it. |
| `make test-entrypoint-identity` | The identity tests against an existing image. |
| `make test-full-image` | Build the full image and check every listed tool. |
| `make docs` | Build this site into `.testing/docs-site`. |
| `make version V=X.Y.Z` | Set the package version without committing. With no `V`, print it. |
| `make clean` | Remove the built images and Python caches. |

## Tests

The unit tests run in-process, without Docker or a real agent. They set `AICODEBOX_ADAPTER=aicodebox.tests.conftest:_StubAdapter`, and the fixtures in [`conftest.py`](https://github.com/psyb0t/docker-aicodebox/blob/main/aicodebox/tests/conftest.py) clear the `AICODEBOX_*` variables a host may have set. They need no credentials and no network once dependencies are installed.

`make test-ci` runs them as your own UID in `ghcr.io/astral-sh/uv:0.13.0-python3.14-trixie-slim`, pinned by digest in the [`Makefile`](https://github.com/psyb0t/docker-aicodebox/blob/main/Makefile). The virtual environment lives inside the container under `/tmp`, so the checkout gets no `.venv` from it, and the workspace root points at a temporary directory because the API modules read it at import time.

The integration tests in `scripts/` need Docker and run throwaway containers without a host Docker socket or credentials. To test a changed entrypoint without rebuilding:

```bash
ENTRYPOINT="$PWD/entrypoint.sh" make test-entrypoint-identity
```

Set `IMAGE` to test another existing image.

Tests with a real agent and a real Telegram chat live in the child repositories, for example the end-to-end harness in [pibox](https://github.com/psyb0t/docker-pibox), which drives Telegram with [telethon-plus](https://github.com/psyb0t/docker-telethon).

## Lint

`make lint` runs flake8 with the settings in [`.flake8`](https://github.com/psyb0t/docker-aicodebox/blob/main/.flake8) (120 columns) and pyright, both from the `lint` dependency group in `pyproject.toml`. Either one failing fails the target.

## Lock files

| Target | Refreshes |
| --- | --- |
| `make pkg-lock` | `uv.lock`, under the `exclude-newer` cutoff in `pyproject.toml`. |
| `make full-go-lock` | `go.sum` for the full image's Go tools. |
| `make full-node-lock` | `full-node/pnpm-lock.yaml`. |
| `make full-python-lock` | `full-python/requirements.txt`, with hashes, from `requirements.in`. |

The Python dependencies the image installs exclude the `dev` and `lint` groups.

## CI

[`pipeline.yml`](https://github.com/psyb0t/docker-aicodebox/blob/main/.github/workflows/pipeline.yml) runs on every push:

| Job | When | Does |
| --- | --- | --- |
| `code` | every branch push | `make lint` and `make test-ci`. |
| `call-docker-image-workflow` | after `code`, on `main` | Builds the minimal image, then the full image on top of it, for amd64 and arm64. Pushes `latest`, `latest-full` and per-commit SHA tags, and scans them. Other branches stop after `code`. |
| `badges` | after `code` on `main`, after `wait` on a tag | Updates the README badges. |
| `wait` | `v*` tag | Waits up to 180 minutes for the tagged commit's `main` run to succeed. |
| `docker-promote` | after `wait` | Gives the release tag to the images that run already built, without rebuilding. |
| `release` | after `docker-promote` | Creates the GitHub Release from the annotated tag's notes. |

## Releases

A release bumps `version` in `pyproject.toml` (`make version V=X.Y.Z`), adds a [`CHANGELOG.md`](https://github.com/psyb0t/docker-aicodebox/blob/main/CHANGELOG.md) entry, lands on `main`, and gets an annotated `vX.Y.Z` tag on that commit. CI does the rest. The package reads its `__version__` from the installed metadata, so there is no second version string to update.

## Docs

The site is Jekyll with the just-the-docs theme, built by GitHub Pages from `docs/` on `main`. `make docs` builds it locally with the same image Pages uses.

- Every page needs front matter with a unique `title` and a `description` of 50 to 170 characters.
- Link between pages with the relative `.md` path. Link a file outside `docs/` with its full GitHub URL, because a relative `../` link breaks on the site.
- Keep `docs/` flat. A file name is its URL.
- A change to behavior, an endpoint or a setting updates its page in the same change.
