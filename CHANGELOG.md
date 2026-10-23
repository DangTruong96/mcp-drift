# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.1] — 2026-10-23

### Fixed
- **Tools-file lockfiles are portable across machines.** The offline
  snapshot identity was a fingerprint of the dump's *absolute path*,
  so the identical lockfile + dump copied to another directory —
  i.e. every CI checkout — reported "server identity changed" and
  exited 1. The identity is now location-independent (transport
  only); the definitions themselves are still diffed in full.
  *Migration:* tools-file lockfiles written by v0.2.0 or earlier will
  report a one-time identity drift on their first check with
  v0.2.1 — re-run `snapshot` once to rewrite the identity.
  (Live-probe lockfiles are unaffected.)
- **Duplicate names in a snapshot source are rejected (exit 2).**
  Two tools (or resources/prompts) sharing a name used to collapse
  silently into a single pinned entry — `tools pinned: 1` for two
  definitions — and changes to the shadowed definition could never
  be detected. Snapshotting now refuses with a clear error, matching
  how `verify` already treats duplicate entries inside a lockfile
  as corruption.
- **Malformed definition entries no longer crash.** A tools file
  whose entries are not JSON objects (a string, a number, …) died
  with a Python traceback and exit code 1 — the *drift* code. Such
  entries, and entries with a non-string `name`, are now clean
  exit-2 errors. The same validation covers definitions served by
  live servers, which funnel through the same code.
- **Malformed client-config entries no longer crash or mislead.**
  A server entry that is not an object, an `env` that is not an
  object, and `args` that is not a list of strings are clean
  exit-2 errors naming the problem. Previously: two of these
  tracebacked, and a string `args` was silently split into single
  characters and launched as a garbage command that then reported
  a misleading "timed out waiting for response".
- 8 regression tests for the above (37 total), including a CLI
  end-to-end check that snapshots in one directory and verifies
  cleanly from another.

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
