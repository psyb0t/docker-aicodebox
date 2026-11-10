---
title: "Full image"
description: "psyb0t/aicodebox:latest-full adds a pinned development toolchain on top of the minimal image: Go tools, Node and Python developer tools, CLIs and clients."
nav_order: 7
has_children: true
---

# Full image

`psyb0t/aicodebox:latest-full` is the minimal image plus a general development toolchain, for agents that need to build and test code in many languages. It is built from [`Dockerfile.full`](https://github.com/psyb0t/docker-aicodebox/blob/main/Dockerfile.full) on top of the minimal image from the same CI run, so both tags of a release carry the same base.

```dockerfile
FROM psyb0t/aicodebox:latest-full
```

The entrypoint, workspace layout, Python package and modes are the same as `latest`. A child image picks one parent per variant and adds only its agent CLI, adapter and configuration. `AICODEBOX_IMAGE_VARIANT=full` is set inside the full image.

## What it adds

| Group | Tools |
| --- | --- |
| Go | Go 1.27.2, golangci-lint 2.14.0, gopls, dlv, staticcheck, gomodifytags, impl, gotests, gofumpt |
| Python | flake8, black, isort, autoflake, pyright, mypy, vulture, pytest, pytest-asyncio, pytest-cov, requests, beautifulsoup4, lxml, pyyaml, toml, pipenv, poetry |
| Node | pnpm, yarn, TypeScript, ts-node, eslint with the TypeScript plugin, prettier, nodemon, pm2, create-react-app, Vue CLI, Angular CLI, express-generator, newman, http-server, serve, lighthouse, Storybook CLI |
| Cloud and repos | gh 2.102.0, Terraform 1.16.5, kubectl 1.37.1, Helm 3.22.0 |
| Build and debug | build-essential, make, cmake, pkg-config, clang-format, valgrind, gdb, strace, ltrace |
| Shell | nano, vim, htop, tmux, zip, unzip, tree, fd, ripgrep, bat, eza, ag, shellcheck, shfmt, httpie, ping, dig |
| Databases | sqlite3, psql, mysql, redis-cli |

## How the versions are pinned

| Source | Pin |
| --- | --- |
| Go toolchain and the release binaries (golangci-lint, gh, Terraform, kubectl, Helm) | Version and SHA-256 per architecture in `Dockerfile.full`. |
| Go tools | [`full-go/`](https://github.com/psyb0t/docker-aicodebox/tree/main/full-go): `go.sum` locks the module graph, `tools.txt` lists what gets built. gopls has its own module in `full-go/gopls/`. |
| Node tools | [`full-node/`](https://github.com/psyb0t/docker-aicodebox/tree/main/full-node): exact versions in `package.json`, a frozen `pnpm-lock.yaml`, install scripts off, and a seven-day minimum release age in `.npmrc`. |
| Python tools | [`full-python/requirements.txt`](https://github.com/psyb0t/docker-aicodebox/blob/main/full-python/requirements.txt), installed with `--require-hashes`, resolved from `requirements.in` with a fixed release-date cutoff. |

The Node tools come with known advisories in their dependency trees. [Optional Node tool advisories](dependency-audit.md) lists them and what they expose. Use the minimal image when you do not need these tools.

## Building it locally

```bash
make build-full
make test-full-image
```

`make build-full` builds the minimal image first and passes it in as `BASE_IMAGE`. `make test-full-image` checks the variant marker, the user, the versions of the main tools and that every listed command runs. The lock refresh targets are in [Development](development.md#lock-files).
