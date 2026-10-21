# Contributing to MCPDrift

Thanks for helping make MCP tool definitions harder to tamper with.

## Ground rules

- **No runtime dependencies.** MCPDrift is standard-library only, and it
  stays that way — a security tool you run against untrusted servers
  should not drag in a supply chain of its own.
- **No telemetry, no network calls** beyond the server you explicitly
  point the tool at.
- **Lockfiles never contain secrets.** Env values, file contents from the
  user's machine beyond the launch descriptor, and credentials must never
  be written into a snapshot. Tests cover this; keep them passing.

## Development

```console
$ git clone https://github.com/DangTruong96/mcp-drift
$ cd mcp-drift
$ python3 -m unittest discover -s tests -v
```

The end-to-end tests spawn `tests/fake_mcp_server.py`, a minimal stdio
MCP server whose behaviour is switched by environment variables
(`FAKE_VARIANT`, `FAKE_ECHO`). If you add a server behaviour worth
pinning, add a variant there and a test that exercises it.

## Pull requests

- One change per PR, with tests for new behaviour.
- Update `CHANGELOG.md` (Unreleased section) and the docs when behaviour
  or the lockfile format changes. **Lockfile format changes are a big
  deal**: they invalidate everyone's baselines, so they need a format
  version bump and a migration note.
- Keep output stable and greppable; exit codes are part of the API
  (0 clean, 1 drift, 2 error).
- By contributing you agree to the [Code of Conduct](CODE_OF_CONDUCT.md)
  and that your contribution is licensed under the MIT License.
