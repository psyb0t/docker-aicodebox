# Docs and releases

Prefix: DOC.

- **DOC1:** `docs/` is the user documentation and `README.md` is the front door. Reference material lives in `docs/`; the README keeps a summary and links to the site with full `https://psyb0t.github.io/docker-aicodebox/...` URLs, because the README is mirrored to Docker Hub.
- **DOC2:** Keep `docs/` flat.
- **DOC3:** After a docs change, run `make docs` and the site link check, and fix what it reports.
- **DOC6:** The version lives in `pyproject.toml` (`make version V=X.Y.Z`). A `vX.Y.Z` tag on a `main` commit makes CI promote the images built for that commit; it never rebuilds on a tag.
