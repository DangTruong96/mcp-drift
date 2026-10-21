# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
