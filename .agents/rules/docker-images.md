# Docker images

Prefix: IMG. Covers `Dockerfile`, `Dockerfile.full`, `entrypoint.sh` and the `full-*` directories.

- **IMG1:** Third-party base images are pinned by digest (`ubuntu:24.04@sha256:...`, `ghcr.io/astral-sh/uv:0.13.0@sha256:...`). Downloaded runtimes and binaries (Python, Node, Go, golangci-lint, gh, Terraform, kubectl, Helm) are pinned by version and SHA-256 per architecture, checked with `sha256sum -c`. Change a version and its hashes together.
- **IMG2:** Both images build for `linux/amd64` and `linux/arm64`. Every architecture `case` rejects anything else with an error.
- **IMG3:** The minimal image stays small. General tooling goes into `Dockerfile.full`, never into `Dockerfile`.
- **IMG4:** `Dockerfile.full` builds `FROM ${BASE_IMAGE}`. CI builds it as stage 1 after the minimal image of the same run, and `make build-full` passes the local minimal tag.
- **IMG5:** Full-image tools come from lock files: `full-go/go.sum` with `tools.txt`, `full-node/pnpm-lock.yaml` installed `--frozen-lockfile --ignore-scripts`, `full-python/requirements.txt` installed `--require-hashes`. Refresh them with the `*-lock` Make targets. A tool added or removed also updates the list in `scripts/test-full-image.sh` and `docs/full-image.md`.
- **IMG6:** The Python package installs from `uv export --frozen --no-dev`, so the `dev` and `lint` groups never reach the image.
- **IMG7:** `entrypoint.sh` runs with `set -euo pipefail`, logs failures as JSON lines through `log`, keeps the init marker in the container filesystem (`/var/lib/aicodebox/init-done`), and never runs the agent as root. Shell changes pass `shellcheck`.
- **IMG8:** `.dockerignore` keeps `docs/`, `.agents/`, test caches and secrets out of the build context. A new top-level directory the image does not need goes there too.
