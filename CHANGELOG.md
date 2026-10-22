# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-10-23

### Added
- `verify` command: offline integrity check of a lockfile. Recomputes
  the top-level fingerprint, every entry fingerprint and every
  per-field hash from the stored definitions and reports each
  mismatch (edited definitions, stale or orphaned field hashes,
  missing field hashes, duplicate entries). Exit codes 0 intact /
  1 edited-or-corrupted / 2 lockfile missing-or-invalid; `--json`
  output supported. `check` and `diff` trust stored fingerprints as
  the baseline — `verify` is how you know the baseline itself is the
  document `snapshot` wrote.
- 13 tests for verification (29 total), including CLI end-to-end runs
  against both tools-file and live fake-server snapshots.

### Fixed
- Live snapshots stored a stale top-level fingerprint: the probe path
  set `server.negotiated_protocol` after the snapshot fingerprint had
  been computed, so the stored document never hashed to its own
  fingerprint. (Offline `--tools-file` snapshots were unaffected.)
  Found by the new `verify` command's end-to-end test; the builder
  and the verifier now share one `_snapshot_fingerprint()` helper so
  they cannot drift apart, and the probe path refreshes the
  fingerprint after the late field is set. Lockfiles written by
  v0.1.0 from live probes will (correctly) fail `verify` on the
  top-level fingerprint only — re-run `snapshot` to rewrite them.

## [0.1.0] — 2026-10-22

### Added
- `snapshot` command: probes a live stdio MCP server (via a command line
  or a client config file — `mcpServers`, `servers` and
  `context_servers` shapes) or reads an offline `tools/list` dump, and
  writes an `mcp-drift.lock.json` lockfile.
- Full-surface canonical fingerprints (SHA-256) for every tool, resource
  and prompt definition — including `title`, `annotations` and
  `outputSchema`, which classic 3-field pinners miss — plus per-field
  hashes for precise diffs.
- Server identity fingerprinting from the launch descriptor (command +
  args + env **keys only**; values are never stored).
- Protocol honesty observations in every snapshot: negotiated protocol
  version, silent-downgrade flag, a bogus-version (`2099-01-01`) echo
  test, and a `server/discover` probe for the stateless 2026-07-28 era.
- `check` command with CI exit codes (0 clean / 1 drift / 2 error) and
  `--json` output; human diffs show before/after text per changed field.
- `diff` (lockfile vs lockfile, offline) and `show` commands.
- A runnable fake MCP server under `examples/` with switchable rug-pull,
  annotations-only and version-echo personalities, plus captured output.
- Test suite (16 tests) including end-to-end subprocess runs against the
  fake server; GitHub Actions CI for Python 3.9–3.12.
