#!/usr/bin/env python3
"""MCPDrift — a lockfile for MCP tool definitions.

Snapshot the tool definitions an MCP server actually serves, fingerprint
them canonically, commit the snapshot as a lockfile, and fail CI when a
server silently changes its tools (a "rug pull"), adds one, or drops one.

Born from a documented gap: Invariant Labs' mcp-scan used to ship
hash-based tool pinning; the feature was removed in its 2026 incarnation
(snyk/agent-scan), and 2026 security guides tell users to hand-roll
snapshot-and-diff scripts instead. The hand-rolled versions pin only
name+description+inputSchema — instructions hidden in ``title``,
``annotations`` or ``outputSchema`` slip past them. MCPDrift pins the
*entire* tool object and reports exactly which field changed.

Stdlib only. Exit codes: 0 = no drift / success, 1 = drift detected,
2 = tool error (server unreachable, bad input, missing lockfile).
"""

import argparse
import hashlib
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
from datetime import datetime, timezone

__version__ = "0.1.0"

LOCK_FORMAT = "mcp-drift-lock"
DEFAULT_LOCK = "mcp-drift.lock.json"
DEFAULT_PROTOCOL = "2025-06-18"
BOGUS_PROTOCOL = "2099-01-01"
CLIENT_INFO = {"name": "mcp-drift", "version": __version__}

# Fields of a tool object, in the order we diff/report them. The
# fingerprint covers the WHOLE object; this list only drives reporting.
TOOL_FIELDS = ("name", "title", "description", "inputSchema",
               "outputSchema", "annotations", "_meta")


class DriftError(Exception):
    """Tool error -> exit code 2."""


# --------------------------------------------------------------------------
# Canonicalisation & fingerprints
# --------------------------------------------------------------------------

def canonical(obj):
    """Stable JSON encoding: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(obj):
    """SHA-256 of the canonical encoding of any JSON value."""
    return sha(canonical(obj))


def tool_entry(tool):
    """One lockfile row for a tool: full definition + fingerprints."""
    field_hashes = {}
    for field in TOOL_FIELDS:
        if field in tool:
            field_hashes[field] = fingerprint(tool[field])
    return {
        "name": tool.get("name", ""),
        "fingerprint": fingerprint(tool),
        "fields": field_hashes,
        "definition": tool,
    }


def entries_by_name(items):
    out = {}
    for item in items or []:
        entry = tool_entry(item)
        out[entry["name"]] = entry
    return out


# --------------------------------------------------------------------------
# Minimal stdio MCP client (JSON-RPC 2.0 over newline-delimited JSON)
# --------------------------------------------------------------------------

class StdioMCPClient:
    def __init__(self, command, args, env=None, cwd=None, timeout=15):
        self.timeout = timeout
        full_env = dict(os.environ)
        if env:
            full_env.update({k: str(v) for k, v in env.items()})
        try:
            self.proc = subprocess.Popen(
                list(command) + list(args),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=full_env, cwd=cwd,
                text=True, encoding="utf-8", bufsize=1)
        except OSError as exc:
            raise DriftError("could not start server: %s" % exc)
        self._q = queue.Queue()
        self._next_id = 0
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._q.put(json.loads(line))
                except ValueError:
                    # Non-JSON on stdout contaminates the transport; the
                    # message is unusable, surface it as a marker.
                    self._q.put({"_stdout_contamination": line})
        except Exception:
            pass

    def _wait_for(self, want_id):
        """Read messages until the response for want_id arrives."""
        import time
        deadline = time.time() + self.timeout
        pending = []
        while time.time() < deadline:
            try:
                msg = self._q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if "_stdout_contamination" in msg:
                raise DriftError(
                    "server wrote non-JSON to stdout (breaks stdio "
                    "transport): %r" % msg["_stdout_contamination"][:120])
            if msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return msg
            pending.append(msg)
        raise DriftError("timed out waiting for response id %s" % want_id)

    def request(self, method, params=None):
        self._next_id += 1
        rid = self._next_id
        payload = {"jsonrpc": "2.0", "id": rid, "method": method,
                   "params": params or {}}
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise DriftError("server closed stdin: %s" % exc)
        msg = self._wait_for(rid)
        if "error" in msg:
            err = msg["error"] or {}
            raise _RpcError(err.get("code", 0), err.get("message", "rpc error"))
        return msg.get("result")

    def notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class _RpcError(DriftError):
    def __init__(self, code, message):
        super().__init__("rpc error %s: %s" % (code, message))
        self.code = code
        self.message = message


METHOD_NOT_FOUND = -32601


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------

def _list_all(client, method, key):
    """Follow pagination cursors for a list method."""
    items, cursor = [], None
    while True:
        params = {}
        if cursor:
            params["cursor"] = cursor
        result = client.request(method, params) or {}
        items.extend(result.get(key, []) or [])
        cursor = result.get("nextCursor")
        if not cursor:
            return items


def initialize_session(command, args, env, cwd, timeout, protocol):
    client = StdioMCPClient(command, args, env=env, cwd=cwd, timeout=timeout)
    try:
        result = client.request("initialize", {
            "protocolVersion": protocol,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        return client, result or {}
    except Exception:
        client.close()
        raise


def probe_server(command, args, env=None, cwd=None, timeout=15):
    """Full probe: negotiate, list, plus version-honesty observations.

    Returns a dict with serverInfo, negotiated protocol, tools/resources/
    prompts (None when the server doesn't offer the method) and an
    ``observations`` dict. Raises DriftError on hard failures.
    """
    observations = {"requested_protocol": DEFAULT_PROTOCOL}
    # --- main session -----------------------------------------------------
    client, init = initialize_session(command, args, env, cwd, timeout,
                                      DEFAULT_PROTOCOL)
    try:
        negotiated = init.get("protocolVersion")
        observations["negotiated_protocol"] = negotiated
        observations["silent_downgrade"] = (
            negotiated is not None and negotiated != DEFAULT_PROTOCOL)
        client.notify("notifications/initialized")
        tools = _list_all(client, "tools/list", "tools")
        try:
            resources = _list_all(client, "resources/list", "resources")
        except _RpcError as exc:
            resources = None if exc.code == METHOD_NOT_FOUND else _raise(exc)
        try:
            prompts = _list_all(client, "prompts/list", "prompts")
        except _RpcError as exc:
            prompts = None if exc.code == METHOD_NOT_FOUND else _raise(exc)
        server_info = init.get("serverInfo") or {}
        capabilities = init.get("capabilities") or {}
    finally:
        client.close()

    # --- bogus-version echo test (fresh session) --------------------------
    observations["version_echo"] = False
    observations["version_echo_detail"] = ""
    try:
        c2, init2 = initialize_session(command, args, env, cwd, timeout,
                                       BOGUS_PROTOCOL)
        try:
            echoed = (init2 or {}).get("protocolVersion")
            if echoed == BOGUS_PROTOCOL:
                observations["version_echo"] = True
                observations["version_echo_detail"] = (
                    "server returned the bogus protocolVersion %s verbatim; "
                    "it does not validate the version it claims" % echoed)
            else:
                observations["version_echo_detail"] = (
                    "server answered %r to a %s request" %
                    (echoed, BOGUS_PROTOCOL))
        finally:
            c2.close()
    except Exception as exc:  # an error to a bogus version is healthy
        observations["version_echo_detail"] = (
            "server rejected the bogus version (%s)" % exc)

    # --- stateless-era discovery probe (fresh session, no handshake) ------
    observations["server_discover"] = "unknown"
    c3 = None
    try:
        c3 = StdioMCPClient(command, args, env=env, cwd=cwd, timeout=timeout)
        try:
            c3.request("server/discover", {})
            observations["server_discover"] = "responded"
        except _RpcError as exc:
            observations["server_discover"] = (
                "method_not_found" if exc.code == METHOD_NOT_FOUND
                else "error_%s" % exc.code)
        except DriftError:
            observations["server_discover"] = "no_response"
    except DriftError:
        observations["server_discover"] = "no_response"
    finally:
        if c3:
            c3.close()

    return {
        "serverInfo": server_info,
        "capabilities": capabilities,
        "negotiated_protocol": negotiated,
        "tools": tools,
        "resources": resources,
        "prompts": prompts,
        "observations": observations,
    }


def _raise(exc):
    raise exc


# --------------------------------------------------------------------------
# Sources: live command, client config file, offline tools dump
# --------------------------------------------------------------------------

def parse_config_server(config_path, server_name):
    """Pull one server's launch descriptor out of a client config JSON.

    Supports the common shapes: {"mcpServers": {...}} (Claude Desktop /
    Cursor / Claude Code), {"servers": {...}} (VS Code) and
    {"context_servers": {...}} (Zed, where command is an object).
    Returns (command_list, args, env_dict, env_keys).
    """
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise DriftError("cannot read config: %s" % exc)
    except ValueError as exc:
        raise DriftError("config is not valid JSON: %s" % exc)
    servers = None
    for key in ("mcpServers", "servers", "context_servers"):
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            servers = data[key]
            break
    if servers is None:
        if isinstance(data, dict) and "command" in data:
            servers = {server_name or "default": data}
        else:
            raise DriftError(
                "no mcpServers/servers/context_servers object in config")
    if server_name is None:
        if len(servers) != 1:
            raise DriftError(
                "config has %d servers; pick one with --server (%s)"
                % (len(servers), ", ".join(sorted(servers))))
        server_name = next(iter(servers))
    if server_name not in servers:
        raise DriftError("server %r not in config (has: %s)"
                         % (server_name, ", ".join(sorted(servers))))
    entry = servers[server_name]
    cmd = entry.get("command")
    args = entry.get("args") or []
    if isinstance(cmd, dict):  # Zed shape: {"path": ..., "args": [...]}
        args = cmd.get("args") or args
        cmd = cmd.get("path") or cmd.get("command")
    if not cmd or not isinstance(cmd, str):
        raise DriftError(
            "server %r has no stdio command (remote/HTTP servers are not "
            "supported by the live probe; use --tools-file instead)"
            % server_name)
    env = entry.get("env") or {}
    return [cmd], list(args), dict(env), server_name


def load_tools_file(path):
    """Load an offline tools dump: a tools/list result or a bare list."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise DriftError("cannot read tools file: %s" % exc)
    except ValueError as exc:
        raise DriftError("tools file is not valid JSON: %s" % exc)
    if isinstance(data, list):
        return data, None, None
    if isinstance(data, dict):
        tools = data.get("tools")
        if tools is None and isinstance(data.get("result"), dict):
            tools = data["result"].get("tools")
        if isinstance(tools, list):
            return tools, data.get("resources"), data.get("prompts")
    raise DriftError("tools file must be a list or contain a 'tools' list")


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def build_snapshot(source, probe=None, tools=None, resources=None,
                   prompts=None, server_info=None, observations=None):
    """Assemble the lockfile document from probed or offline data."""
    tools_map = entries_by_name(tools)
    snap = {
        "format": LOCK_FORMAT,
        "version": 1,
        "tool": "mcp-drift %s" % __version__,
        "created_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "server": source,
        "observations": observations or {},
        "tools": [tools_map[name] for name in sorted(tools_map)],
    }
    if server_info:
        snap["server"] = dict(source, serverInfo=server_info)
    for label, items in (("resources", resources), ("prompts", prompts)):
        if items is not None:
            m = entries_by_name(items)
            snap[label] = [m[n] for n in sorted(m)]
    core = {k: snap[k] for k in snap if k not in ("created_at", "tool")}
    snap["fingerprint"] = fingerprint(core)
    return snap


def snapshot_from_probe(command, args, env, env_keys, name, timeout,
                        cwd=None):
    probe = probe_server(command, args, env=env, cwd=cwd, timeout=timeout)
    descriptor = {
        "transport": "stdio",
        "command": command[0] if command else "",
        "args": list(args),
        "env_keys": sorted(env_keys or []),
    }
    if name:
        descriptor["name"] = name
    identity_src = dict(descriptor)
    descriptor["identity"] = fingerprint(identity_src)
    snap = build_snapshot(
        descriptor, tools=probe["tools"], resources=probe["resources"],
        prompts=probe["prompts"], server_info=probe["serverInfo"],
        observations=probe["observations"])
    snap["server"]["negotiated_protocol"] = probe["negotiated_protocol"]
    return snap


def snapshot_from_tools_file(path):
    tools, resources, prompts = load_tools_file(path)
    descriptor = {"transport": "tools-file", "path": os.path.abspath(path)}
    descriptor["identity"] = fingerprint(descriptor)
    return build_snapshot(descriptor, tools=tools, resources=resources,
                          prompts=prompts,
                          observations={"source": "offline tools dump"})


def write_snapshot(snap, out_path):
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError:
        raise DriftError("lockfile not found: %s (run `snapshot` first)"
                         % path)
    except ValueError as exc:
        raise DriftError("lockfile is not valid JSON: %s" % exc)
    if not isinstance(data, dict) or data.get("format") != LOCK_FORMAT:
        raise DriftError("%s is not an mcp-drift lockfile" % path)
    return data


# --------------------------------------------------------------------------
# Diffing
# --------------------------------------------------------------------------

def _entry_map(snap, section):
    return {e["name"]: e for e in snap.get(section) or []}


def diff_section(old_snap, new_snap, section):
    """Field-level diff of one section (tools/resources/prompts)."""
    old, new = _entry_map(old_snap, section), _entry_map(new_snap, section)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = []
    for name in sorted(set(old) & set(new)):
        o, n = old[name], new[name]
        if o["fingerprint"] == n["fingerprint"]:
            continue
        fields = sorted(
            f for f in set(o.get("fields", {})) | set(n.get("fields", {}))
            if o.get("fields", {}).get(f) != n.get("fields", {}).get(f))
        if not fields:  # something outside the reported fields changed
            fields = ["<other fields>"]
        field_diffs = {}
        for f in fields:
            if f.startswith("<"):
                continue
            ov = o.get("definition", {}).get(f)
            nv = n.get("definition", {}).get(f)
            field_diffs[f] = {"before": ov, "after": nv}
        changed.append({"name": name, "fields": fields,
                        "field_diffs": field_diffs})
    return {"added": added, "removed": removed, "changed": changed}


def diff_snapshots(old_snap, new_snap):
    diff = {}
    drift = False
    for section in ("tools", "resources", "prompts"):
        if old_snap.get(section) is None and new_snap.get(section) is None:
            continue
        d = diff_section(old_snap, new_snap, section)
        diff[section] = d
        if d["added"] or d["removed"] or d["changed"]:
            drift = True
    old_srv = old_snap.get("server") or {}
    new_srv = new_snap.get("server") or {}
    srv_changes = {}
    for key in ("identity", "serverInfo", "negotiated_protocol",
                "command", "args"):
        if old_srv.get(key) != new_srv.get(key):
            srv_changes[key] = {"before": old_srv.get(key),
                                "after": new_srv.get(key)}
    if srv_changes:
        diff["server"] = srv_changes
        if "serverInfo" in srv_changes or "identity" in srv_changes:
            drift = True
    diff["drift"] = drift
    return diff


def _short(value, limit=220):
    text = value if isinstance(value, str) else canonical(value)
    return text if len(text) <= limit else text[:limit] + "…"


def render_diff(diff, old_label="lockfile", new_label="current"):
    lines = []
    if not diff["drift"] and not diff.get("server"):
        return ["No drift: definitions match the %s exactly." % old_label]
    for section in ("tools", "resources", "prompts"):
        d = diff.get(section)
        if not d:
            continue
        for name in d["added"]:
            lines.append("[+] %s added: %s" % (section[:-1], name))
        for name in d["removed"]:
            lines.append("[-] %s removed: %s" % (section[:-1], name))
        for ch in d["changed"]:
            lines.append("[~] %s changed: %s (fields: %s)"
                         % (section[:-1], ch["name"],
                            ", ".join(ch["fields"])))
            for field, fd in ch["field_diffs"].items():
                lines.append("      %s before: %s" % (field, _short(fd["before"])))
                lines.append("      %s after:  %s" % (field, _short(fd["after"])))
    for key, change in (diff.get("server") or {}).items():
        lines.append("[!] server %s changed: %s -> %s"
                     % (key, _short(change["before"], 120),
                        _short(change["after"], 120)))
    if diff["drift"]:
        lines.append("DRIFT DETECTED — review the changes, then re-run "
                     "`snapshot` to accept them as the new baseline.")
    return lines


# --------------------------------------------------------------------------
# Source resolution from CLI args
# --------------------------------------------------------------------------

def resolve_live_source(args):
    """Return (command, args, env, env_keys, name) for live probing."""
    if args.config:
        command, sargs, env, name = parse_config_server(args.config,
                                                         args.server)
        return command, sargs, env, sorted(env), name
    if args.cmd:
        parts = list(args.cmd)
        if len(parts) == 1 and " " in parts[0]:
            parts = shlex.split(parts[0])
        if not parts:
            raise DriftError("empty server command")
        return [parts[0]], parts[1:], {}, [], args.server
    raise DriftError(
        "no server source: pass a command after --, or --config/--server, "
        "or --tools-file")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_snapshot(args):
    if args.tools_file:
        snap = snapshot_from_tools_file(args.tools_file)
    else:
        command, sargs, env, env_keys, name = resolve_live_source(args)
        snap = snapshot_from_probe(command, sargs, env, env_keys, name,
                                   args.timeout)
    write_snapshot(snap, args.out)
    obs = snap.get("observations") or {}
    print("Snapshot written to %s" % args.out)
    print("  server identity: %s" % (snap["server"].get("identity", "")[:16]))
    print("  tools pinned: %d" % len(snap.get("tools") or []))
    if snap.get("resources") is not None:
        print("  resources pinned: %d" % len(snap["resources"]))
    if snap.get("prompts") is not None:
        print("  prompts pinned: %d" % len(snap["prompts"]))
    print("  snapshot fingerprint: %s" % snap["fingerprint"][:16])
    if obs.get("version_echo"):
        print("  WARNING: %s" % obs.get("version_echo_detail", ""))
    if obs.get("silent_downgrade"):
        print("  NOTE: server negotiated %s (asked %s)"
              % (obs.get("negotiated_protocol"),
                 obs.get("requested_protocol")))
    if obs.get("server_discover"):
        print("  server/discover (stateless era): %s"
              % obs["server_discover"])
    return 0


def cmd_check(args):
    lock = load_snapshot(args.lock)
    if args.tools_file:
        current = snapshot_from_tools_file(args.tools_file)
    else:
        command, sargs, env, env_keys, name = resolve_live_source(args)
        current = snapshot_from_probe(command, sargs, env, env_keys, name,
                                      args.timeout)
    diff = diff_snapshots(lock, current)
    if args.json:
        print(json.dumps(diff, indent=2, ensure_ascii=False))
    else:
        for line in render_diff(diff):
            print(line)
        obs = current.get("observations") or {}
        if obs.get("version_echo"):
            print("WARNING: %s" % obs.get("version_echo_detail", ""))
    return 1 if diff["drift"] else 0


def cmd_diff(args):
    old = load_snapshot(args.old)
    new = load_snapshot(args.new)
    diff = diff_snapshots(old, new)
    if args.json:
        print(json.dumps(diff, indent=2, ensure_ascii=False))
    else:
        for line in render_diff(diff, old_label=args.old,
                                new_label=args.new):
            print(line)
    return 1 if diff["drift"] else 0


def cmd_show(args):
    snap = load_snapshot(args.lock)
    if args.json:
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return 0
    srv = snap.get("server") or {}
    print("Server: %s %s" % (srv.get("command", srv.get("transport", "?")),
                             " ".join(srv.get("args") or [])))
    info = srv.get("serverInfo") or {}
    if info:
        print("  serverInfo: %s %s" % (info.get("name", ""),
                                       info.get("version", "")))
    print("  identity: %s" % srv.get("identity", ""))
    if srv.get("negotiated_protocol"):
        print("  negotiated protocol: %s" % srv["negotiated_protocol"])
    for section in ("tools", "resources", "prompts"):
        entries = snap.get(section)
        if entries is None:
            continue
        print("%s (%d):" % (section.capitalize(), len(entries)))
        for e in entries:
            desc = (e.get("definition", {}).get("description") or "")
            desc = desc.split("\n")[0][:72]
            print("  %-28s %s  %s" % (e["name"], e["fingerprint"][:12], desc))
    obs = snap.get("observations") or {}
    if obs:
        print("Observations:")
        for key in sorted(obs):
            print("  %s: %s" % (key, obs[key]))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mcp-drift",
        description="Lockfile for MCP tool definitions: snapshot what a "
                    "server serves, then fail CI when it silently changes "
                    "(rug pulls, poisoned edits, silent downgrades).")
    parser.add_argument("--version", action="version",
                        version="mcp-drift %s" % __version__)
    parser.add_argument("--timeout", type=float, default=15,
                        help="per-request timeout in seconds (default 15)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_source(sp):
        sp.add_argument("--config", help="client config JSON "
                        "(mcpServers/servers/context_servers)")
        sp.add_argument("--server", help="server name inside --config")
        sp.add_argument("--tools-file",
                        help="offline tools/list dump (JSON) instead of "
                             "probing a live server")
        sp.add_argument("cmd", nargs=argparse.REMAINDER,
                        help="server command after --, e.g. "
                             "-- python3 server.py")

    sp = sub.add_parser("snapshot", help="probe a server and write a lockfile")
    add_source(sp)
    sp.add_argument("-o", "--out", default=DEFAULT_LOCK,
                    help="lockfile path (default %s)" % DEFAULT_LOCK)
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("check", help="re-probe and compare to the lockfile")
    add_source(sp)
    sp.add_argument("--lock", default=DEFAULT_LOCK,
                    help="lockfile path (default %s)" % DEFAULT_LOCK)
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("diff", help="compare two lockfiles offline")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.add_argument("--json", action="store_true", help="JSON output")
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("show", help="print a lockfile in human form")
    sp.add_argument("--lock", default=DEFAULT_LOCK)
    sp.add_argument("--json", action="store_true", help="raw JSON")
    sp.set_defaults(func=cmd_show)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    # argparse.REMAINDER keeps a leading "--"; drop it.
    if getattr(args, "cmd", None) and args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    try:
        return args.func(args)
    except DriftError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
