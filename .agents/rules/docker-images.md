# Docker images

Prefix: IMG. Covers `Dockerfile`, `Dockerfile.full`, `entrypoint.sh` and the `full-*` directories.

- **IMG2:** Both images build for `linux/amd64` and `linux/arm64`. Every architecture `case` rejects anything else with an error.
- **IMG3:** The minimal image stays small. General tooling goes into `Dockerfile.full`, never into `Dockerfile`.
- **IMG4:** `Dockerfile.full` builds `FROM ${BASE_IMAGE}`. CI builds it as stage 1 after the minimal image of the same run, and `make build-full` passes the local minimal tag.
- **IMG5:** Full-image tools come from lock files: `full-go/go.sum` with `tools.txt`, `full-node/pnpm-lock.yaml` installed `--frozen-lockfile --ignore-scripts`, `full-python/requirements.txt` installed `--require-hashes`. Refresh them with the `*-lock` Make targets. A tool added or removed also updates the list in `scripts/test-full-image.sh` and `docs/full-image.md`.
- **IMG6:** The Python package installs from `uv export --frozen --no-dev`, so the `dev` and `lint` groups never reach the image.
- **IMG7:** `entrypoint.sh` logs failures as JSON lines through `log`, keeps the init marker in the container filesystem (`/var/lib/aicodebox/init-done`), and never runs the agent as root.
- **IMG8:** `.dockerignore` keeps `docs/`, `.agents/`, test caches and secrets out of the build context. A new top-level directory the image does not need goes there too.
