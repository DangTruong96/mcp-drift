# MCPDrift — Usage Guide

## Concepts

**Snapshot.** A JSON *lockfile* recording what an MCP server served at
approval time: every tool/resource/prompt definition in full, a canonical
SHA-256 fingerprint per item and per field, the server's launch identity,
and protocol observations.

**Pinning.** Comparing a fresh probe against the snapshot. Any difference
— added, removed, or changed definitions — is *drift* and fails the
check (exit 1) until a human re-snapshots.

**Full-surface.** Fingerprints cover the *entire* definition object
(`name`, `title`, `description`, `inputSchema`, `outputSchema`,
`annotations`, `_meta`, and anything else the server sends), encoded with
sorted keys so formatting changes don't create false drift.

**Identity.** A snapshot is keyed to a fingerprint of the launch
descriptor — transport + command + args + sorted env **keys**. Env
*values* (secrets) are never written to the lockfile. Two entries with
the same display name but different commands are different identities.

## Commands

Global: `--timeout SECONDS` (per-request, default 15), `--version`.

Every probing command takes exactly one source:

| Source | How |
|---|---|
| Live command | `-- python3 server.py --flag` (everything after `--`) |
| Client config | `--config PATH [--server NAME]` — reads `mcpServers` (Claude Desktop/Cursor/Claude Code), `servers` (VS Code), or `context_servers` (Zed). With several servers, `--server` is required. |
| Offline dump | `--tools-file PATH` — a JSON file holding a `tools/list` result (`{"tools": [...]}`), a full JSON-RPC response, or a bare list. May also carry `resources`/`prompts`. |

### `snapshot [-o mcp-drift.lock.json]`

Probes the server and writes the lockfile. Live probes run three
sessions: (1) negotiate `2025-06-18`, `tools/list` (+ resources/prompts
when offered, following pagination); (2) a fresh session asking for the
bogus version `2099-01-01` — if the server returns it verbatim, the
snapshot records `version_echo: true` and the CLI prints a warning;
(3) a `server/discover` request with no handshake, recording whether the
server answers in the stateless 2026-07-28 style. Observations are
advisory; they never change the exit code of `snapshot` (0 on success,
2 if the server cannot be probed).

### `check [--lock PATH] [--json]`

Re-probes and diffs against the lockfile. Exit codes:

| Code | Meaning |
|---|---|
| 0 | No drift. |
| 1 | Drift: something was added, removed, changed, or the server identity/serverInfo changed. |
| 2 | Error: server unreachable, timed out, lockfile missing/invalid, bad input. |

Human output shows added/removed items and, for changed items, the
fields and before/after values (long values truncated at 220 chars).
`--json` prints the full structured diff.

Server *observations* (negotiated version, echo result, discover
result) are stored in the snapshot but only `serverInfo` and `identity`
changes count as drift — a server moving to a new protocol era is
something you accept by re-snapshotting, and the diff shows it under
`server` changes for review.

### `diff OLD_LOCK NEW_LOCK [--json]`

Compares two lockfiles offline — e.g. the one in a PR against the one on
your main branch. Same exit codes as `check` (minus unreachable errors).

### `show [--lock PATH] [--json]`

Human summary of a lockfile: server, identity, negotiated protocol, each
pinned item with its short fingerprint and first description line, and
all observations. `--json` prints the raw lockfile.

## Lockfile format (version 1)

```json
{
  "format": "mcp-drift-lock",
  "version": 1,
  "tool": "mcp-drift 0.1.0",
  "created_at": "2026-10-22T00:00:00Z",
  "server": {
    "transport": "stdio",
    "command": "python3",
    "args": ["server.py"],
    "env_keys": ["API_KEY"],
    "identity": "<sha256 of the descriptor above>",
    "serverInfo": {"name": "…", "version": "…"},
    "negotiated_protocol": "2025-06-18"
  },
  "observations": { "version_echo": false, "server_discover": "method_not_found", "…": "…" },
  "tools": [
    {"name": "…", "fingerprint": "<sha256 of canonical full definition>",
     "fields": {"description": "<sha256>", "inputSchema": "<sha256>"},
     "definition": { "…": "the full tool object, verbatim" }}
  ],
  "resources": [ "…same shape, when the server offers resources/list…" ],
  "fingerprint": "<sha256 over the lockfile core>"
}
```

Definitions are stored verbatim so diffs can show before/after text.
Before committing a lockfile to a *public* repo, skim the definitions —
they are exactly what the server sends, and a badly behaved server could
put sensitive text in a description. (Your own secrets are safe:
MCPDrift never stores env values or anything from your machine beyond
the launch command you pointed it at.)

## CI recipe

```yaml
name: mcp-drift
on: [push, pull_request]
jobs:
  pin-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install mcp-drift   # or from your vendored copy
      - run: mcp-drift check --config .mcp.json --server my-server
```

A failing check means: read the diff in the job log, and if the new
definitions are intended, run `mcp-drift snapshot` and commit the
updated lockfile in the same pull request — the lockfile diff *is* the
review.

## Troubleshooting

- **"server wrote non-JSON to stdout"** — the server (or a dependency)
  prints logs to stdout, which corrupts the stdio transport. This is the
  single most common MCP breakage; the server must log to stderr.
- **Exit 2 with a timeout** — the server may need a first-run download
  (`npx -y` cold starts). Raise `--timeout`, or warm the cache first.
- **Drift on every run** — some servers embed timestamps or random ids
  in descriptions/schemas. That is itself a finding (unstable
  definitions can't be reviewed); consider pinning a fixed server
  version, and please open an issue with the lockfile's diff.
