#!/usr/bin/env python3
"""Exercise real repository preparation from local archives and tracked labels.

Run with --bazel PATH. These metadata-only consumer queries use native
filegroups, with no language rules, compiler downloads or host patch commands.

The contract is an ordered archive/patch chain with tracked label inputs and
explicit relative series identity. Fixtures independently define the resulting
file bytes. Tests catch reordered stages, basename-only matching, stale inputs,
ignored checksum/patch failures and paths escaping the prepared repository.
"""

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


def archive_bytes(files):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode()
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))
    return result.getvalue()


def patch(old, new):
    return "--- a/state.txt\n+++ b/state.txt\n@@ -1 +1 @@\n-" + old + "\n+" + new + "\n"


class PreparedSourceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="prepared-source-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        shutil.copyfile(SOURCE / "prepared_source.bzl", self.workspace / "prepared_source.bzl")
        self.build_file = 'exports_files(glob(["**"], exclude = ["BUILD.bazel"]))\n'
        (self.workspace / "source.BUILD.bazel").write_text(self.build_file)
        (self.workspace / "BUILD.bazel").write_text('exports_files(glob(["**"]))\n')
        self.inputs = {}
        self.write_input("framing.patch", patch("alpha", "beta"))
        self.write_input("local/series", "# same basenames must retain their directories\n\none/change.patch\ntwo/change.patch\n")
        self.write_input("local/one/change.patch", patch("gamma", "delta"))
        self.write_input("local/two/change.patch", patch("delta", "epsilon"))
        self.steps = [
            self.archive("source.tar.gz", {"upstream/state.txt": "alpha\n", "upstream/archive-order.txt": "first archive\n", "upstream/vendor.tar.gz": archive_bytes({"nested/header.h": "nested payload\n"})}, strip_prefix="upstream"),
            self.archive("packaging.tar.gz", {"archive-order.txt": "last archive wins\n", "debian/patches/series": "# packaging\n\na.patch\n", "debian/patches/a.patch": patch("beta", "gamma")}),
            {"op": "patch", "input": "framing.patch", "strip": 1},
            {"op": "patch_series", "series": "debian/patches/series"},
            {"op": "patch_series", "series_input": "local/series", "patch_inputs": ["local/one/change.patch", "local/two/change.patch"]},
            {"op": "extract", "archive": "vendor.tar.gz", "output": "vendor", "strip_prefix": "nested"},
        ]
        self.command = [BAZEL, "--batch", "--ignore_all_rc_files", "--output_user_root=" + str(self.root / "user"), "--output_base=" + str(self.root / "output"),
                        "query", "--lockfile_mode=off", "--incompatible_autoload_externally=",
                        "--repository_cache=" + str(self.root / "cache"), "--output=location", "@prepared//..."]

    def write_input(self, name, content):
        target = self.workspace / "inputs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.inputs[name] = "//:inputs/" + name

    def archive(self, name, files, **options):
        target = self.root / name
        target.write_bytes(archive_bytes(files))
        return {"op": "archive", "urls": [target.as_uri()], "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), **options}

    def query(self, steps=None, error=None):
        actual_steps = self.steps if steps is None else steps
        (self.workspace / "MODULE.bazel").write_text(
            'module(name = "prepared_source_test")\n'
            'prepared_source = use_repo_rule("//:prepared_source.bzl", "prepared_source")\n'
            'prepared_source(name = "prepared", build_file = "//:source.BUILD.bazel", inputs = ' + repr(self.inputs) + ', steps = ' + repr([json.dumps(step) for step in actual_steps]) + ')\n')
        result = subprocess.run(self.command, cwd=self.workspace, capture_output=True, text=True, timeout=60)
        if error:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            if isinstance(error, str):
                self.assertIn(error, result.stderr)
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        state = next((self.root / "output/external").glob("*/state.txt"))
        return state.parent

    def test_ordered_archives_patch_stages_and_nested_extraction(self):
        prepared = self.query()
        self.assertEqual((prepared / "state.txt").read_text(), "epsilon\n")
        self.assertEqual((prepared / "archive-order.txt").read_text(), "last archive wins\n")
        self.assertEqual((prepared / "vendor/header.h").read_text(), "nested payload\n")
        self.assertEqual((prepared / "BUILD.bazel").read_text(), self.build_file)
        # Stage order is observable: the series patch expects framing's beta.
        reordered = copy.deepcopy(self.steps)
        reordered[2], reordered[3] = reordered[3], reordered[2]
        self.query(reordered, error=True)
        self.assertEqual((self.query() / "state.txt").read_text(), "epsilon\n")

    def test_declared_patch_series_and_build_changes_invalidate_repository(self):
        self.assertEqual((self.query() / "state.txt").read_text(), "epsilon\n")
        self.write_input("local/two/change.patch", patch("delta", "changed"))
        self.assertEqual((self.query() / "state.txt").read_text(), "changed\n")
        # Change only the series label, without modifying MODULE or patches.
        self.write_input("local/series", "one/change.patch\n")
        self.query(error="patch_inputs must match the ordered full relative series paths")
        shortened = copy.deepcopy(self.steps)
        shortened[4]["patch_inputs"] = ["local/one/change.patch"]
        self.assertEqual((self.query(shortened) / "state.txt").read_text(), "delta\n")
        self.build_file += '# caller build injection changed\n'
        (self.workspace / "source.BUILD.bazel").write_text(self.build_file)
        self.assertEqual((self.query(shortened) / "BUILD.bazel").read_text(), self.build_file)

    def test_full_series_paths_checksum_and_missing_inputs(self):
        wrong_paths = copy.deepcopy(self.steps)
        wrong_paths[4]["patch_inputs"].reverse()
        self.query(wrong_paths, error="ordered full relative series paths")
        bad_hash = copy.deepcopy(self.steps)
        bad_hash[0]["sha256"] = "0" * 64
        self.query(bad_hash, error=True)
        missing = copy.deepcopy(self.steps)
        missing[2]["input"] = "undeclared.patch"
        self.query(missing, error="source input is not declared")
        (self.workspace / "inputs/local/two/change.patch").unlink()
        self.query(error=True)

    def test_unified_multifile_hunks_payload_and_patch_invalidation(self):
        # Plain unified diff has file headers but no Git separator. The first
        # hunk deliberately contains payload that looks like another header.
        source = self.archive("unified.tar.gz", {
            "state.txt": "before\n-- a/not-a-file\nfinish\n",
            "second.txt": "top\nmiddle\nbottom\n",
        })
        content = (
            "--- a/state.txt\n+++ b/state.txt\n@@ -1,3 +1,3 @@\n"
            " before\n--- a/not-a-file\n+++ b/not-a-file\n finish\n"
            "--- a/second.txt\n+++ b/second.txt\n@@ -1 +1 @@\n-top\n+TOP\n"
            "@@ -3 +3 @@\n-bottom\n+BOTTOM\n"
        )
        self.write_input("plain.patch", content)
        steps = [source, {"op": "patch", "input": "plain.patch"}]
        # Reproduce Bazel's adjacent plain-file-section limitation, rather than
        # silently falling back after a native application failure.
        self.query(steps, error=True)
        steps[1]["format"] = "unified"
        prepared = self.query(steps)
        self.assertEqual((prepared / "state.txt").read_text(), "before\n++ b/not-a-file\nfinish\n")
        self.assertEqual((prepared / "second.txt").read_text(), "TOP\nmiddle\nBOTTOM\n")
        self.assertFalse((prepared / ".prepared_source_unified_patch").exists())
        self.assertEqual((self.workspace / "inputs/plain.patch").read_text(), content)
        self.write_input("plain.patch", content.replace("+BOTTOM", "+UPDATED"))
        self.assertEqual((self.query(steps) / "second.txt").read_text(), "TOP\nmiddle\nUPDATED\n")

    def test_unified_series_creates_and_deletes_files(self):
        source = self.archive("create-delete.tar.gz", {"state.txt": "unchanged\n", "old.txt": "obsolete\n"})
        content = (
            "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+created\n"
            "--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-obsolete\n"
        )
        self.write_input("local/create-delete.patch", content)
        self.write_input("local/series", "create-delete.patch\n")
        steps = [source, {"op": "patch_series", "series_input": "local/series", "patch_inputs": ["local/create-delete.patch"], "format": "unified"}]
        prepared = self.query(steps)
        self.assertEqual((prepared / "new.txt").read_text(), "created\n")
        self.assertFalse((prepared / "old.txt").exists())
        self.assertEqual((prepared / "state.txt").read_text(), "unchanged\n")
        self.assertFalse((prepared / ".prepared_source_unified_patch").exists())

    def test_unified_context_truncation_metadata_and_scratch_failures(self):
        source = self.archive("invalid-unified.tar.gz", {"state.txt": "alpha\n"})
        steps = [source, {"op": "patch", "input": "invalid.patch", "format": "unified"}]
        self.write_input("invalid.patch", patch("wrong context", "beta"))
        self.query(steps, error=True)
        self.write_input("invalid.patch", "--- a/state.txt\n+++ b/state.txt\n@@ -1,2 +1,2 @@\n alpha\n")
        self.query(steps, error="truncated unified hunk")
        self.write_input("invalid.patch", patch("alpha", "beta") + "+overflow\n")
        self.query(steps, error="unified hunk line counts disagree")
        self.write_input("invalid.patch", "diff --git a/state.txt b/state.txt\n" + patch("alpha", "beta"))
        self.query(steps, error="Git metadata and binary patches require format=native")
        self.write_input("invalid.patch", patch("alpha", "beta"))
        self.assertEqual((self.query(steps) / "state.txt").read_text(), "beta\n")
        # A caller's existing file at the scratch path must be protected.
        steps[0] = self.archive("scratch-collision.tar.gz", {"state.txt": "alpha\n", ".prepared_source_unified_patch": "source data\n"})
        self.query(steps, error="conflicts with reserved patch scratch path")
        self.write_input("local/series", "# empty series\n")
        self.query([source, {"op": "patch_series", "series_input": "local/series", "patch_inputs": [], "format": "unknown"}], error="patch format must be native or unified")

    def test_reject_unknown_operations_keys_unsafe_paths_and_series_options(self):
        invalid = [
            ([{"op": "command", "command": "echo"}], "unknown source operation"),
            ([{"op": "extract", "archive": "vendor.tar.gz", "unexpected": 1}], "unknown source step key"),
            ([{"op": "extract", "archive": "../outside.tar.gz"}], "relative path"),
            ([{"op": "extract", "archive": "/outside.tar.gz"}], "relative path"),
            ([{"op": "extract", "archive": "vendor.tar.gz", "output": "../outside"}], "relative path"),
            ([{"op": "archive", "urls": ["file:///missing"], "sha256": "0" * 64, "strip_prefix": "../prefix"}], "relative path"),
            ([{"op": "patch", "input": "framing.patch", "strip": -1}], "non-negative integer"),
            ([{"op": "patch_series", "series": "a", "series_input": "b"}], "exactly one"),
        ]
        for steps, error in invalid:
            with self.subTest(steps=steps):
                self.query(steps, error=error)
        for series in ("one/change.patch -p1\n", "../outside.patch\n", "/absolute.patch\n", "one/change.patch\none/change.patch\n"):
            with self.subTest(series=series):
                self.write_input("local/series", series)
                self.query(error="unsupported patch series arguments" if "-p1" in series else "duplicate patch series path" if series.count("one/change.patch") == 2 else "relative path")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    options, remaining = parser.parse_known_args()
    BAZEL = str(Path(shutil.which(options.bazel) or options.bazel).resolve())
    unittest.main(argv=[sys.argv[0], *remaining])
