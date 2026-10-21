# MCPDrift — Self-Evaluation (v0.1.0)

Self-audit against the standard for a credible open-source release, done
before publishing. Every row was verified locally; nothing here is
rounded up. Date: 2026-10-22.

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | OSI license present | PASS | `LICENSE` (MIT, © 2026 DangTruong96) |
| 2 | Working code, real CLI | PASS | `mcp_drift.py`: snapshot/check/diff/show, `--help`, exit codes 0/1/2 all exercised in tests and the captured drill |
| 3 | Automated tests pass | PASS | `python3 -m unittest discover -s tests` → **16 tests, OK** (unit + subprocess end-to-end against a fake stdio MCP server) |
| 4 | End-to-end smoke test | PASS | Tests spawn the real CLI against `tests/fake_mcp_server.py`: snapshot → clean check (0), description rug pull (1), annotations-only change (1), version-echo flagged, dead server (2), missing lockfile (2), lockfile diff (0/1) |
| 5 | Installable package | PASS | Fresh venv: `pip install .` succeeds; `mcp-drift` entry point reports `mcp-drift 0.1.0` and runs snapshot/check offline |
| 6 | CI configured | PASS | `.github/workflows/ci.yml`: Python 3.9–3.12 matrix, unittests + CLI smoke incl. live snapshot/check against the example server. First execution on the publish push (2026-10-21): **green** — 3 consecutive success runs, latest 20:46:03 UTC. |
| 7 | README quality | PASS | Problem + sourced demand evidence, install, usage, exit codes, honest "What it is not", roadmap, maintainer/governance, license |
| 8 | Deep docs | PASS | `docs/USAGE.md`: concepts, full command/flag reference, lockfile format v1 spec, CI recipe, troubleshooting |
| 9 | Real demo output | PASS | `examples/README.md` contains output captured from actual runs (snapshot, clean check, v2/v3 drift, echo warning, show) — regenerated during this build, not invented; `examples/` is fully runnable |
| 10 | Governance | PASS | `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md` (private reporting via GitHub Security Advisories), issue templates, PR template |
| 11 | Changelog + semver | PASS | `CHANGELOG.md` v0.1.0 entry; version `0.1.0` in `pyproject.toml` and the CLI |
| 12 | Safety property holds | PASS | Test asserts a config env **value** never appears in a built snapshot (keys only) |
| 13 | No placeholders / fake claims | PASS | Repo-wide sweep for TODO/FIXME/placeholder/lorem → zero matches (excluding this file). No badges claiming stars/CI status/users anywhere; README states v0.1.0 scope limits plainly |
| 14 | Public repo + release | PASS | Published 2026-10-21: https://github.com/DangTruong96/mcp-drift (created 20:17 UTC), release `v0.1.0` (20:46 UTC), topics set (`ai-agents`, `ci`, `cli`, `mcp`, `model-context-protocol`, `python`, `rug-pull`, `security`). |

**Totals: 14 PASS · 0 FAIL · 0 PENDING.** All publish-dependent rows verified against the live repository on 2026-10-22.

## Known limitations, disclosed rather than hidden

- Definition drift only: a server that keeps its manifest identical but
  changes behaviour server-side is invisible to any pinning tool
  (documented in README *What it is not*).
- Live probing is stdio-only in v0.1.0; HTTP servers use `--tools-file`.
- `server/discover` is probed and recorded, but full 2026-07-28
  stateless-era negotiation is roadmap (v0.2), not claimed here.
- The project has no external users yet; the README makes no adoption
  claims.
