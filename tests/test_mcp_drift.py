"""Tests for mcp_drift: unit + end-to-end against a fake MCP server."""
import json
import os
import shutil
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


class TestVerify(unittest.TestCase):
    """verify_lockfile: the lockfile's stored hashes must recompute."""

    TOOLS = [
        {"name": "a", "description": "one",
         "inputSchema": {"type": "object"},
         "annotations": {"title": "A"}},
        {"name": "b", "description": "two", "inputSchema": {}},
    ]

    def snap(self):
        return md.build_snapshot({"transport": "test"},
                                 tools=json.loads(json.dumps(self.TOOLS)))

    def test_intact_snapshot_passes(self):
        self.assertEqual(md.verify_lockfile(self.snap()), [])

    def test_roundtripped_snapshot_passes(self):
        # What verify actually sees in production: JSON out and back in.
        snap = json.loads(json.dumps(self.snap()))
        self.assertEqual(md.verify_lockfile(snap), [])

    def test_tampered_definition_flagged(self):
        snap = self.snap()
        snap["tools"][0]["definition"]["description"] = "one (edited)"
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("entry fingerprint" in p for p in problems))
        self.assertTrue(any("top-level" in p for p in problems))

    def test_tampered_field_hash_flagged(self):
        snap = self.snap()
        snap["tools"][0]["fields"]["description"] = "0" * 64
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("field hash mismatch" in p for p in problems))

    def test_tampered_entry_fingerprint_flagged(self):
        snap = self.snap()
        snap["tools"][1]["fingerprint"] = "0" * 64
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("entry fingerprint" in p for p in problems))

    def test_tampered_top_level_flagged(self):
        snap = self.snap()
        snap["fingerprint"] = "0" * 64
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("top-level" in p for p in problems))

    def test_missing_field_hash_flagged(self):
        snap = self.snap()
        del snap["tools"][0]["fields"]["annotations"]
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("no field hash stored" in p for p in problems))

    def test_field_hash_for_absent_field_flagged(self):
        snap = self.snap()
        snap["tools"][1]["fields"]["title"] = md.fingerprint("ghost")
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("absent from the definition" in p
                            for p in problems))

    def test_duplicate_names_flagged(self):
        snap = self.snap()
        snap["tools"].append(dict(snap["tools"][0]))
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("duplicate entry" in p for p in problems))


class TestVerifyCommand(unittest.TestCase):
    """CLI-level: verify exit codes on tools-file and live snapshots."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.lock = os.path.join(self.td.name, "lock.json")
        self.dump = os.path.join(self.td.name, "tools.json")
        with open(self.dump, "w") as fh:
            json.dump({"tools": [
                {"name": "a", "description": "one", "inputSchema": {}}
            ]}, fh)

    def tearDown(self):
        self.td.cleanup()

    def test_verify_clean_lockfile_exit_0(self):
        r = run_cli(["snapshot", "--tools-file", self.dump,
                     "-o", self.lock])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        r = run_cli(["verify", "--lock", self.lock])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("Lockfile OK", r.stdout)

    def test_verify_tampered_lockfile_exit_1(self):
        r = run_cli(["snapshot", "--tools-file", self.dump,
                     "-o", self.lock])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        with open(self.lock, encoding="utf-8") as fh:
            snap = json.load(fh)
        snap["tools"][0]["definition"]["description"] = "one (edited)"
        with open(self.lock, "w", encoding="utf-8") as fh:
            json.dump(snap, fh)
        r = run_cli(["verify", "--lock", self.lock])
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("FAILED verification", r.stdout)
        r = run_cli(["verify", "--lock", self.lock, "--json"])
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_verify_missing_lockfile_exit_2(self):
        r = run_cli(["verify", "--lock", self.lock])
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)

    def test_verify_live_snapshot_exit_0(self):
        server = [sys.executable, FAKE]
        r = run_cli(["snapshot", "-o", self.lock, "--"] + server)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        r = run_cli(["verify", "--lock", self.lock])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("2 tools", r.stdout)


class TestToolsFilePortability(unittest.TestCase):
    """A committed tools-file lockfile must check clean on any machine.

    Regression: the offline identity used to be a fingerprint of the
    dump's ABSOLUTE path, so the identical lockfile + dump copied to
    another directory (i.e. every CI checkout) reported
    'server identity changed' and exited 1.
    """

    TOOLS = [{"name": "a", "description": "one", "inputSchema": {}}]

    def test_same_content_same_identity_at_any_path(self):
        with tempfile.TemporaryDirectory() as td:
            snaps = []
            for sub in ("one", "two"):
                d = os.path.join(td, sub)
                os.makedirs(d)
                p = os.path.join(d, "dump.json")
                with open(p, "w") as fh:
                    json.dump({"tools": self.TOOLS}, fh)
                snaps.append(md.snapshot_from_tools_file(p))
            self.assertEqual(snaps[0]["server"]["identity"],
                             snaps[1]["server"]["identity"])
            self.assertFalse(md.diff_snapshots(*snaps)["drift"])

    def test_cli_check_clean_from_another_directory(self):
        with tempfile.TemporaryDirectory() as td:
            dir_a = os.path.join(td, "author-machine", "repo")
            dir_b = os.path.join(td, "ci-runner", "checkout")
            for d in (dir_a, dir_b):
                os.makedirs(d)
                with open(os.path.join(d, "tools.json"), "w") as fh:
                    json.dump({"tools": self.TOOLS}, fh)
            lock = os.path.join(dir_a, "lock.json")
            r = run_cli(["snapshot", "--tools-file",
                         os.path.join(dir_a, "tools.json"), "-o", lock])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            shutil.copy(lock, os.path.join(dir_b, "lock.json"))
            r = run_cli(["check", "--tools-file", "tools.json",
                         "--lock", "lock.json"], cwd=dir_b)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("No drift", r.stdout)


class TestMalformedInputs(unittest.TestCase):
    """Malformed sources: clean exit-2 errors, never tracebacks, and
    never a silently lossy snapshot."""

    def write(self, obj, raw=None):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False)
        if raw is not None:
            fh.write(raw)
        else:
            json.dump(obj, fh)
        fh.close()
        return fh.name

    def test_duplicate_tool_names_rejected(self):
        # Regression: duplicates used to collapse silently into one
        # pinned entry ("tools pinned: 1" for two definitions), and
        # changes to the shadowed definition could never be detected.
        tools = [
            {"name": "dup", "description": "first", "inputSchema": {}},
            {"name": "dup", "description": "second", "inputSchema": {}},
        ]
        with self.assertRaises(md.DriftError):
            md.build_snapshot({"transport": "test"}, tools=tools)
        path = self.write({"tools": tools})
        out = path + ".lock"
        r = run_cli(["snapshot", "--tools-file", path, "-o", out])
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("duplicate name", r.stderr)
        self.assertFalse(os.path.exists(out))

    def test_non_object_entries_rejected(self):
        # Regression: these used to die with an AttributeError /
        # TypeError traceback and exit code 1 (the "drift" code).
        for raw in ('["just-a-string"]', '[42]'):
            path = self.write(None, raw=raw)
            r = run_cli(["snapshot", "--tools-file", path,
                         "-o", path + ".lock"])
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertNotIn("Traceback", r.stderr)

    def test_non_string_name_rejected(self):
        path = self.write({"tools": [{"name": 123, "description": "x"}]})
        r = run_cli(["snapshot", "--tools-file", path,
                     "-o", path + ".lock"])
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("non-string name", r.stderr)

    def test_config_entry_not_an_object(self):
        path = self.write({"mcpServers": {"x": "https://example.com"}})
        with self.assertRaises(md.DriftError):
            md.parse_config_server(path, "x")

    def test_config_args_must_be_a_list_of_strings(self):
        # A string 'args' used to be split into single characters and
        # launched as a garbage command that then "timed out".
        for args in ("srv.py", [123]):
            path = self.write({"mcpServers": {"x": {
                "command": "python3", "args": args}}})
            with self.assertRaises(md.DriftError):
                md.parse_config_server(path, "x")

    def test_config_env_must_be_an_object(self):
        path = self.write({"mcpServers": {"x": {
            "command": "python3", "args": [], "env": ["A=1"]}}})
        with self.assertRaises(md.DriftError):
            md.parse_config_server(path, "x")


if __name__ == "__main__":
    unittest.main()


class TestAdversarialRound2(unittest.TestCase):
    """Round-2 adversarial pass: hostile nesting, structurally mangled
    lockfiles, and entry-order insensitivity of `verify`."""

    def write(self, obj, raw=None):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False)
        if raw is not None:
            fh.write(raw)
        else:
            json.dump(obj, fh)
        fh.close()
        return fh.name

    def snap(self, tools):
        return md.build_snapshot({"transport": "test"}, tools=tools)

    def test_deeply_nested_tools_file_is_clean_error(self):
        # Regression: a tools dump nested thousands of levels deep
        # used to die with an uncaught RecursionError traceback and
        # exit 1. Built as raw text: json.dump cannot even write it.
        depth = 3000
        body = ('{"type": "object", "properties": {"n": ' * depth
                + '{"type": "string"}' + '}}' * depth)
        raw = ('{"tools": [{"name": "deep", "description": "d", '
               '"inputSchema": ' + body + '}]}')
        path = self.write(None, raw=raw)
        r = run_cli(["snapshot", "--tools-file", path,
                     "-o", path + ".lock"])
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("nested too deeply", r.stderr)

    def test_deeply_nested_lockfile_is_clean_error(self):
        depth = 20000
        body = ('{"type": "object", "properties": {"n": ' * depth
                + '{"type": "string"}' + '}}' * depth)
        raw = ('{"format": "mcp-drift-lock", "tools": [{"name": "x", '
               '"fingerprint": "0", "fields": {}, "definition": '
               '{"name": "x", "inputSchema": ' + body + '}}]}')
        path = self.write(None, raw=raw)
        r = run_cli(["verify", "--lock", path])
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_verify_reports_non_object_entry(self):
        # Regression: verify — the command whose whole job is
        # diagnosing corrupt lockfiles — crashed with AttributeError
        # on a lockfile whose entry was a bare string.
        snap = self.snap([{"name": "a", "description": "one",
                           "inputSchema": {}}])
        snap["tools"].append("just-a-string")
        problems = md.verify_lockfile(snap)
        self.assertTrue(any("not an object" in p for p in problems),
                        problems)
        path = self.write(snap)
        r = run_cli(["verify", "--lock", path])
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not an object", r.stdout)

    def test_malformed_entry_clean_error_in_diff_check_show(self):
        # Regression: check/diff/show died with TypeError/KeyError
        # tracebacks (exit 1) on entries that are not objects or lack
        # a stored fingerprint.
        good = self.snap([{"name": "a", "description": "one",
                           "inputSchema": {}}])
        string_entry = json.loads(json.dumps(good))
        string_entry["tools"][0] = "just-a-string"
        no_fp = json.loads(json.dumps(good))
        del no_fp["tools"][0]["fingerprint"]
        for mangled in (string_entry, no_fp):
            bad_path = self.write(mangled)
            good_path = self.write(good)
            for argv in (["diff", bad_path, good_path],
                         ["show", "--lock", bad_path]):
                r = run_cli(argv)
                self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("malformed entry", r.stderr)

    def test_malformed_definition_fields_clean_error(self):
        # Regression (QA loop 2026-10-29, mcp-drift): a lockfile entry
        # whose 'definition' is a string/list crashed show with an
        # AttributeError traceback, crashed diff the same way once
        # both the entry fingerprint and a field hash differed, and
        # an entry whose 'fields' is a string/list crashed verify —
        # the command whose whole job is diagnosing corrupt
        # lockfiles — with an AttributeError traceback. All three
        # must now be clean errors / clean problem reports.
        good = self.snap([{"name": "a", "description": "one",
                           "inputSchema": {}}])
        def_string = json.loads(json.dumps(good))
        def_string["tools"][0]["definition"] = "not-an-object"
        def_string["tools"][0]["fingerprint"] = "0" * 64
        def_string["tools"][0]["fields"]["description"] = "0" * 64
        fields_string = json.loads(json.dumps(good))
        fields_string["tools"][0]["fields"] = "bogus"
        fields_list = json.loads(json.dumps(good))
        fields_list["tools"][0]["fields"] = ["description"]
        for mangled in (def_string, fields_string, fields_list):
            bad_path = self.write(mangled)
            good_path = self.write(good)
            for argv in (["diff", bad_path, good_path],
                         ["show", "--lock", bad_path]):
                r = run_cli(argv)
                self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("malformed entry", r.stderr)
            r = run_cli(["verify", "--lock", bad_path])
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("not an object", r.stdout)
        # verify on a fields-mangled lockfile exits 1 (corrupt
        # baseline), not 2: the lockfile parses, its content is bad.
        r = run_cli(["verify", "--lock", self.write(fields_string)])
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)

    def test_duplicate_lockfile_entry_refused_by_diff_check_show(self):
        # Regression (QA loop 2026-10-29, mcp-drift): a lockfile with
        # two entries for one name silently kept only the last, so
        # when the shadowed (first) entry differed and the good copy
        # came last, diff reported "No drift" while verify flagged
        # the duplicate — the same collapse fixed for snapshot
        # sources in v0.2.1. diff/check/show must now refuse the
        # ambiguous baseline with a clean exit-2 error.
        good = self.snap([{"name": "a", "description": "one",
                           "inputSchema": {}}])
        dup = json.loads(json.dumps(good))
        bad = dict(dup["tools"][0])
        bad["fingerprint"] = "0" * 64
        dup["tools"] = [bad, dup["tools"][0]]
        # Sanity: verify still flags it, and the old diff behaviour
        # (before this fix) reported no drift for this exact file.
        self.assertTrue(any("duplicate entry" in p
                            for p in md.verify_lockfile(dup)))
        bad_path = self.write(dup)
        good_path = self.write(good)
        dump_path = self.write({"tools": [
            {"name": "a", "description": "one", "inputSchema": {}}]})
        for argv in (["diff", bad_path, good_path],
                     ["show", "--lock", bad_path],
                     ["check", "--tools-file", dump_path,
                      "--lock", bad_path]):
            r = run_cli(argv)
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("duplicate entry", r.stderr)

    def test_verify_ignores_entry_order(self):
        # Regression: the top-level fingerprint was the only
        # order-sensitive value in the tool, so a lockfile whose
        # entries were merely reordered (formatter, merge tool)
        # failed `verify` while `check` reported no drift.
        tools = [{"name": n, "description": "d " + n, "inputSchema": {}}
                 for n in ("alpha", "beta", "gamma", "delta")]
        snap = self.snap(tools)
        reordered = json.loads(json.dumps(snap))
        reordered["tools"] = list(reversed(reordered["tools"]))
        self.assertEqual(md.verify_lockfile(reordered), [])
        path = self.write(reordered)
        r = run_cli(["verify", "--lock", path])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_verify_still_catches_reorder_plus_tamper(self):
        tools = [{"name": n, "description": "d " + n, "inputSchema": {}}
                 for n in ("alpha", "beta")]
        snap = self.snap(tools)
        evil = json.loads(json.dumps(snap))
        evil["tools"] = list(reversed(evil["tools"]))
        evil["tools"][0]["definition"]["description"] = "pwned"
        problems = md.verify_lockfile(evil)
        self.assertTrue(any("fingerprint" in p for p in problems),
                        problems)


class TestQaRound3ScaleFuzz(unittest.TestCase):
    """Round-3 QA pass (2026-11-10): scale & performance stress,
    fuzz/property testing of the canonical fingerprint, and
    idempotence — the angle this round used instead of re-running
    the round-2 adversarial/malformed-input angle."""

    def write(self, obj, raw=None):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False)
        if raw is not None:
            fh.write(raw)
        else:
            json.dump(obj, fh)
        fh.close()
        return fh.name

    # --- lone surrogates (defect 1) -----------------------------------
    def test_lone_surrogate_definition_end_to_end(self):
        # Regression: a description containing a JSON-legal lone
        # surrogate escape (\ud800 — servable by any MCP server over
        # stdio, or present in a tools dump) used to crash snapshot,
        # verify, show and check with an uncaught UnicodeEncodeError
        # traceback, exit 1. Every command must now handle it cleanly.
        tools = [{"name": "evil", "description": "pwn\ud800ed",
                  "inputSchema": {"type": "object"}}]
        # json.dump defaults to ensure_ascii=True, so the file carries
        # the escape "\ud800" — exactly the JSON-legal form a server
        # or a tools dump would carry.
        dump = self.write({"tools": tools})
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, "lock.json")
            r = run_cli(["snapshot", "--tools-file", dump, "-o", lock])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertNotIn("Traceback", r.stderr)
            for argv in (["verify", "--lock", lock],
                         ["verify", "--lock", lock, "--json"],
                         ["show", "--lock", lock],
                         ["show", "--lock", lock, "--json"],
                         ["check", "--tools-file", dump, "--lock", lock]):
                r = run_cli(argv)
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                self.assertNotIn("Traceback", r.stderr)
            # --json output is still valid JSON and round-trips.
            r = run_cli(["show", "--lock", lock, "--json"])
            shown = json.loads(r.stdout)
            self.assertEqual(
                shown["tools"][0]["definition"]["description"],
                "pwn\ud800ed")
            # Snapshotting the same dump twice is deterministic.
            lock2 = os.path.join(td, "lock2.json")
            r = run_cli(["snapshot", "--tools-file", dump, "-o", lock2])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            fp1 = md.load_snapshot(lock)["fingerprint"]
            fp2 = md.load_snapshot(lock2)["fingerprint"]
            self.assertEqual(fp1, fp2)

    def test_fingerprint_surrogate_is_not_sanitized_away(self):
        # The fix must not "solve" surrogates by replacing them with
        # U+FFFD: that would make two different served definitions
        # (\ud800 vs \ufffd) pin to the same fingerprint, i.e. a
        # change the tool could never detect.
        self.assertNotEqual(md.fingerprint("a\ud800b"),
                            md.fingerprint("a\ufffdb"))
        self.assertNotEqual(md.fingerprint("a\ud800b"),
                            md.fingerprint("ab"))
        self.assertNotEqual(md.fingerprint("\ud800"),
                            md.fingerprint("\udc00"))
        # And fingerprints of surrogate-free values are unchanged by
        # the fix (checked against a value pinned in an earlier
        # release's lockfile semantics: sha256 of the canonical form).
        import hashlib
        self.assertEqual(
            md.fingerprint({"a": 1}),
            hashlib.sha256(b'{"a":1}').hexdigest())

    # --- fingerprint properties (fuzz) --------------------------------
    def test_fingerprint_property_random_json(self):
        # Property fuzz: canonical encoding must be key-order
        # insensitive and stable across a JSON round-trip, for
        # arbitrary JSON values (300 seeded trials).
        import random
        rng = random.Random(20261110)

        def rand_json(depth=0):
            r = rng.random()
            if depth > 4 or r < 0.3:
                return rng.choice([None, True, False, 0, 1, -1, 1.5,
                                   1e16, 0.1, "", "a", "é🎉",
                                   "x" * 50, 2 ** 70, -0.0])
            if r < 0.65:
                return [rand_json(depth + 1)
                        for _ in range(rng.randint(0, 4))]
            keys = rng.sample(["a", "b", "c", "name", "description",
                               "x", "é", ""], rng.randint(0, 4))
            return {k: rand_json(depth + 1) for k in keys}

        def shuffle_keys(o):
            if isinstance(o, dict):
                items = list(o.items())
                rng.shuffle(items)
                return {k: shuffle_keys(v) for k, v in items}
            if isinstance(o, list):
                return [shuffle_keys(v) for v in o]
            return o

        for _ in range(300):
            v = rand_json()
            self.assertEqual(md.fingerprint(v),
                             md.fingerprint(shuffle_keys(v)))
            self.assertEqual(md.fingerprint(v),
                             md.fingerprint(json.loads(md.canonical(v))))

    # --- idempotence / scale ------------------------------------------
    def test_snapshot_repeat_identical_except_timestamp(self):
        # Idempotence: snapshotting the same dump twice must produce
        # the same lockfile except for `created_at` (second
        # resolution) — and the same top-level fingerprint. A naive
        # byte comparison is timing-flaky across a second boundary,
        # so compare parsed documents instead.
        tools = [{"name": "a", "description": "one", "inputSchema": {}}]
        dump = self.write({"tools": tools})
        with tempfile.TemporaryDirectory() as td:
            a = os.path.join(td, "a.json")
            b = os.path.join(td, "b.json")
            self.assertEqual(run_cli(["snapshot", "--tools-file", dump,
                                      "-o", a]).returncode, 0)
            self.assertEqual(run_cli(["snapshot", "--tools-file", dump,
                                      "-o", b]).returncode, 0)
            ja = md.load_snapshot(a)
            jb = md.load_snapshot(b)
            self.assertEqual(ja["fingerprint"], jb["fingerprint"])
            ja.pop("created_at")
            jb.pop("created_at")
            self.assertEqual(ja, jb)

    def test_scale_2000_tools_roundtrip(self):
        tools = [{"name": "tool_%05d" % i,
                  "description": "Tool number %d does things." % i,
                  "inputSchema": {
                      "type": "object",
                      "properties": {"q": {"type": "string"}},
                      "required": ["q"]},
                  "annotations": {"title": "Tool %d" % i,
                                  "readOnlyHint": True}}
                 for i in range(2000)]
        dump = self.write({"tools": tools})
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, "lock.json")
            r = run_cli(["snapshot", "--tools-file", dump, "-o", lock])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("2000", r.stdout)
            r = run_cli(["verify", "--lock", lock])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            r = run_cli(["check", "--tools-file", dump, "--lock", lock])
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("No drift", r.stdout)

    # --- unbounded server input (defects 2 and 3) ---------------------
    def test_pagination_loop_refused(self):
        # Regression: a server that always returns nextCursor made
        # snapshot loop forever (48,726 pages fetched in 20 s in the
        # repro; the per-request timeout never fires because each
        # page answers instantly). It must now refuse cleanly.
        server = (
            "import json, sys\n"
            "n = 0\n"
            "for line in sys.stdin:\n"
            "    try: msg = json.loads(line)\n"
            "    except ValueError: continue\n"
            "    m, mid = msg.get('method'), msg.get('id')\n"
            "    if m == 'initialize':\n"
            "        r = {'jsonrpc': '2.0', 'id': mid, 'result': "
            "{'protocolVersion': '2025-06-18', 'capabilities': "
            "{'tools': {}}, 'serverInfo': {'name': 'bomb', "
            "'version': '1'}}}\n"
            "    elif m == 'tools/list':\n"
            "        n += 1\n"
            "        r = {'jsonrpc': '2.0', 'id': mid, 'result': "
            "{'tools': [{'name': 't%d' % n, 'description': 'p', "
            "'inputSchema': {'type': 'object'}}], "
            "'nextCursor': 'c%d' % n}}\n"
            "    elif mid is not None:\n"
            "        r = {'jsonrpc': '2.0', 'id': mid, 'error': "
            "{'code': -32601, 'message': 'nf'}}\n"
            "    else: continue\n"
            "    sys.stdout.write(json.dumps(r) + chr(10))\n"
            "    sys.stdout.flush()\n")
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["--timeout", "5", "snapshot", "-o",
                         os.path.join(td, "lock.json"), "--",
                         sys.executable, "-c", server])
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("past 100 pages", r.stderr)

    def test_notification_flood_memory_bounded(self):
        # Regression: every unmatched message (notification, or a
        # response to another id) was appended to a dead `pending`
        # list that nothing ever read, so a server flooding 100k
        # notifications before answering grew the tool to ~111 MB
        # peak RSS. Unmatched messages are now dropped; peak RSS
        # must stay near the ~18 MB baseline.
        import time
        server = (
            "import json, sys, os\n"
            "N = int(os.environ.get('FLOOD_N', '100000'))\n"
            "for line in sys.stdin:\n"
            "    try: msg = json.loads(line)\n"
            "    except ValueError: continue\n"
            "    m, mid = msg.get('method'), msg.get('id')\n"
            "    if m == 'initialize':\n"
            "        for i in range(N):\n"
            "            sys.stdout.write(json.dumps({'jsonrpc': '2.0', "
            "'method': 'notifications/message', 'params': "
            "{'level': 'info', 'data': 'flood-%d-%s' % (i, 'x'*120)}}) "
            "+ chr(10))\n"
            "        sys.stdout.flush()\n"
            "        r = {'jsonrpc': '2.0', 'id': mid, 'result': "
            "{'protocolVersion': '2025-06-18', 'capabilities': "
            "{'tools': {}}, 'serverInfo': {'name': 'flood', "
            "'version': '1'}}}\n"
            "    elif m == 'tools/list':\n"
            "        r = {'jsonrpc': '2.0', 'id': mid, 'result': "
            "{'tools': []}}\n"
            "    elif mid is not None:\n"
            "        r = {'jsonrpc': '2.0', 'id': mid, 'error': "
            "{'code': -32601, 'message': 'nf'}}\n"
            "    else: continue\n"
            "    sys.stdout.write(json.dumps(r) + chr(10))\n"
            "    sys.stdout.flush()\n")
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, FLOOD_N="100000")
            proc = subprocess.Popen(
                CLI + ["--timeout", "30", "snapshot", "-o",
                       os.path.join(td, "lock.json"), "--",
                       sys.executable, "-c", server],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env)
            peak_kb = 0
            while proc.poll() is None:
                try:
                    with open("/proc/%d/status" % proc.pid) as fh:
                        for ln in fh:
                            if ln.startswith("VmHWM:"):
                                peak_kb = max(peak_kb,
                                              int(ln.split()[1]))
                except OSError:
                    pass
                time.sleep(0.05)
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, err + out)
            self.assertLess(peak_kb, 60 * 1024,
                            "peak RSS %d KB for a 100k-notification "
                            "flood" % peak_kb)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
