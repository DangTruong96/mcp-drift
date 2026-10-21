# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| < 0.1 | No |

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use
GitHub's private vulnerability reporting (Security → Report a
vulnerability) on this repository, so the report stays confidential
until a fix is available.

## Notes specific to this tool

- MCPDrift runs the MCP server command **you** give it, with your
  privileges — that is inherent to probing a stdio server. Only snapshot
  servers you intend to run anyway, and treat `check` the same way.
- Lockfiles deliberately store tool definitions verbatim (needed for
  human diffs) but **never** env values or other local secrets. If you
  find a path where local data leaks into a snapshot, that is a security
  bug — please report it as above.
- A clean `mcp-drift check` means "definitions match what you approved",
  not "this server is safe". Pair it with a description scanner and
  runtime controls (see the README's *What it is not*).
