"""Tests for mcp_drift: unit + end-to-end against a fake MCP server."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mcp_drift as md

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FAKE = os.path.join(ROOT, "tests", "fake_mcp_server.py")
CLI = [sys.executable, os.path.join(ROOT, "mcp_drift.py")]


def run_cli(argv, env_extra=None, cwd=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(CLI + argv, capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=60)


class TestCanonical(unittest.TestCase):
    def test_key_order_irrelevant(self):
        a = {"b": 1, "a": {"y": 2, "x": 1}}
        b = {"a": {"x": 1, "y": 2}, "b": 1}
        self.assertEqual(md.fingerprint(a), md.fingerprint(b))

    def test_full_surface_annotations_count(self):
        """The documented gap of 3-field pinners: annotations must count."""
        base = {"name": "t", "description": "d",
                "inputSchema": {"type": "object"},
                "annotations": {"title": "T"}}
        changed = json.loads(json.dumps(base))
        changed["annotations"]["title"] = "T (trusted)"
        self.assertNotEqual(md.fingerprint(base), md.fingerprint(changed))
        e_base, e_changed = md.tool_entry(base), md.tool_entry(changed)
        self.assertIn("annotations",
                      md.diff_snapshots(
                          md.build_snapshot({}, tools=[base]),
                          md.build_snapshot({}, tools=[changed]),
                      )["tools"]["changed"][0]["fields"])

    def test_field_hashes_present(self):
        e = md.tool_entry({"name": "x", "description": "hi",
                           "inputSchema": {}})
        self.assertEqual(set(e["fields"]),
                         {"name", "description", "inputSchema"})


class TestDiff(unittest.TestCase):
    def snap(self, tools):
        return md.build_snapshot({"transport": "test"}, tools=tools)

    def test_added_removed_changed(self):
        t1 = {"name": "a", "description": "one", "inputSchema": {}}
        t2 = {"name": "b", "description": "two", "inputSchema": {}}
        t2b = {"name": "b", "description": "two!", "inputSchema": {}}
        t3 = {"name": "c", "description": "three", "inputSchema": {}}
        d = md.diff_snapshots(self.snap([t1, t2]), self.snap([t2b, t3]))
        self.assertTrue(d["drift"])
        self.assertEqual(d["tools"]["added"], ["c"])
        self.assertEqual(d["tools"]["removed"], ["a"])
        self.assertEqual([c["name"] for c in d["tools"]["changed"]], ["b"])
        self.assertEqual(d["tools"]["changed"][0]["field_diffs"]
                         ["description"]["after"], "two!")

    def test_no_drift(self):
        t = {"name": "a", "description": "one", "inputSchema": {}}
        d = md.diff_snapshots(self.snap([t]), self.snap([dict(t)]))
        self.assertFalse(d["drift"])


class TestConfigParsing(unittest.TestCase):
    def write(self, obj):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False)
        json.dump(obj, fh)
        fh.close()
        return fh.name

    def test_mcpservers_shape_and_env_keys_only(self):
        path = self.write({"mcpServers": {"fake": {
            "command": "python3", "args": ["srv.py"],
            "env": {"API_KEY": "secret-value"}}}})
        cmd, args, env, name = md.parse_config_server(path, None)
        self.assertEqual((cmd, args, name), (["python3"], ["srv.py"], "fake"))
        # env values must never reach a snapshot: build one via probe data
        source = {"transport": "stdio", "command": cmd[0], "args": args,
                  "env_keys": sorted(env)}
        built = md.build_snapshot(source, tools=[])
        self.assertNotIn("secret-value", json.dumps(built))
        self.assertEqual(built["server"]["env_keys"], ["API_KEY"])

    def test_vscode_servers_shape(self):
        path = self.write({"servers": {"x": {"command": "node",
                                             "args": ["s.js"]}}})
        cmd, args, env, name = md.parse_config_server(path, "x")
        self.assertEqual(cmd, ["node"])

    def test_remote_entry_rejected(self):
        path = self.write({"mcpServers": {"r": {"url": "https://x/mcp"}}})
        with self.assertRaises(md.DriftError):
            md.parse_config_server(path, "r")


class TestToolsFile(unittest.TestCase):
    def test_roundtrip_no_drift_then_drift(self):
        tools = [{"name": "a", "description": "one", "inputSchema": {}}]
        with tempfile.TemporaryDirectory() as td:
            dump = os.path.join(td, "tools.json")
            with open(dump, "w") as fh:
                json.dump({"tools": tools}, fh)
            snap = md.snapshot_from_tools_file(dump)
            lock = os.path.join(td, "lock.json")
            md.write_snapshot(snap, lock)
            r = run_cli(["check", "--tools-file", dump, "--lock", lock])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            tools[0]["description"] = "one — now with a hidden instruction"
            with open(dump, "w") as fh:
                json.dump({"tools": tools}, fh)
            r = run_cli(["check", "--tools-file", dump, "--lock", lock])
            self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
            self.assertIn("description", r.stdout)


class TestEndToEnd(unittest.TestCase):
    """Spawn the CLI against the fake stdio MCP server."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.lock = os.path.join(self.td.name, "lock.json")
        self.server = [sys.executable, FAKE]

    def tearDown(self):
        self.td.cleanup()

    def snapshot(self, env_extra=None):
        return run_cli(["snapshot", "-o", self.lock, "--"] + self.server,
                       env_extra=env_extra)

    def check(self, env_extra=None):
        return run_cli(["check", "--lock", self.lock, "--"] + self.server,
                       env_extra=env_extra)

    def test_snapshot_then_check_clean(self):
        r = self.snapshot()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.exists(self.lock))
        snap = md.load_snapshot(self.lock)
        self.assertEqual([t["name"] for t in snap["tools"]],
                         ["add", "search_docs"])
        self.assertEqual(len(snap["resources"]), 1)
        self.assertIsNone(snap.get("prompts"))
        self.assertEqual(snap["observations"]["negotiated_protocol"],
                         "2025-06-18")
        self.assertFalse(snap["observations"]["version_echo"])
        self.assertEqual(snap["observations"]["server_discover"],
                         "method_not_found")
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("No drift", r.stdout)

    def test_description_rug_pull_detected(self):
        self.assertEqual(self.snapshot().returncode, 0)
        r = self.check(env_extra={"FAKE_VARIANT": "v2"})
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("search_docs", r.stdout)
        self.assertIn("description", r.stdout)
        self.assertIn("API keys", r.stdout)  # the injected sentence is shown

    def test_annotations_only_change_detected(self):
        """v3 changes ONLY annotations.title — 3-field pinners miss this."""
        self.assertEqual(self.snapshot().returncode, 0)
        r = self.check(env_extra={"FAKE_VARIANT": "v3"})
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("annotations", r.stdout)

    def test_version_echo_flagged(self):
        r = self.snapshot(env_extra={"FAKE_ECHO": "1"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        snap = md.load_snapshot(self.lock)
        self.assertTrue(snap["observations"]["version_echo"])
        self.assertIn("WARNING", r.stdout)

    def test_check_missing_lock_is_error_2(self):
        r = run_cli(["check", "--lock", self.lock, "--"] + self.server)
        self.assertEqual(r.returncode, 2)

    def test_dead_server_is_error_2(self):
        r = run_cli(["snapshot", "-o", self.lock, "--",
                     sys.executable, "-c", "pass"])
        self.assertEqual(r.returncode, 2)

    def test_diff_command_between_lockfiles(self):
        self.assertEqual(self.snapshot().returncode, 0)
        other = os.path.join(self.td.name, "lock2.json")
        r = run_cli(["snapshot", "-o", other, "--"] + self.server,
                    env_extra={"FAKE_VARIANT": "v2"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        r = run_cli(["diff", self.lock, other])
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        r = run_cli(["diff", self.lock, self.lock])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
