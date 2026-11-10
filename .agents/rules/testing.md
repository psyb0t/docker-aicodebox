# Testing

Prefix: TEST. How to run each suite is in `AGENTS.md` and `docs/development.md`.

- **TEST1:** Unit tests live in `aicodebox/tests/`, run in-process, and use the stub adapter from `conftest.py` (`stub_adapter` fixture). They never start a real agent, call Telegram, or need network or credentials. `make test-ci` must stay green and deterministic, because CI runs it.
- **TEST2:** Use the `tmp_home` and `tmp_workspace` fixtures for anything that writes state. The autouse `_clean_env` fixture clears host `AICODEBOX_*` variables; add a new variable there when tests read it.
- **TEST3:** Behavior that needs a real container (UID remapping, socket GID, init scripts, `bin/` on `PATH`, Git identity) is tested in `scripts/test-entrypoint-hooks.sh` and `scripts/test-entrypoint-identity.sh`, without a host Docker socket or credentials. A change to `entrypoint.sh` runs `make test-integration`.
- **TEST4:** A bug fix comes with a unit test that fails without it.
- **TEST5:** End-to-end tests with a real agent and a real Telegram chat belong to the child repositories, not here.
