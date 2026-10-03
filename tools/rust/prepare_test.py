#!/usr/bin/env python3
"""Regression checks for locked Cargo metadata preparation; no build actions."""

import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("cargo_prepare", Path(__file__).with_name("prepare.py"))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


CARGO = b'''version = 4
[[package]]
name = "app"
version = "0.1.0"
[[package]]
name = "serde"
version = "1.0.228"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "feedbeef"
[[package]]
name = "common"
version = "0.1.0"
source = "git+https://example.org/common.git?rev=abcd#abcd"
'''


def graph():
    return {"crates": {
        "app 0.1.0": {"name": "app", "version": "0.1.0"},
        "serde 1.0.228": {"name": "serde", "version": "1.0.228",
                          "repository": {"Http": {"sha256": "feedbeef"}}},
        "common 0.1.0": {"name": "common", "version": "0.1.0",
                         "repository": {"Git": {"remote": "https://example.org/common.git",
                                                 "commitish": {"Rev": "abcd"}}}},
    }}


class GraphTest(unittest.TestCase):
    def test_matching_registry_git_and_workspace_pins(self):
        self.assertEqual(len(prepare.verify_graph(CARGO, graph())), 3)

    def test_registry_checksum_mismatch_is_rejected(self):
        value = graph()
        value["crates"]["serde 1.0.228"]["repository"]["Http"]["sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "Cargo.lock pin"):
            prepare.verify_graph(CARGO, value)

    def test_version_change_is_rejected_even_if_source_lock_is_unchanged(self):
        value = graph()
        value["crates"]["serde 1.0.228"]["version"] = "1.0.229"
        with self.assertRaisesRegex(ValueError, "Cargo.lock pin"):
            prepare.verify_graph(CARGO, value)

    def test_git_revision_change_is_rejected(self):
        value = graph()
        value["crates"]["common 0.1.0"]["repository"]["Git"]["commitish"] = {"Rev": "new"}
        with self.assertRaisesRegex(ValueError, "Cargo.lock pin"):
            prepare.verify_graph(CARGO, value)

    def test_registry_dependency_cannot_become_local(self):
        value = graph()
        del value["crates"]["serde 1.0.228"]["repository"]
        with self.assertRaisesRegex(ValueError, "Cargo.lock pin"):
            prepare.verify_graph(CARGO, value)


class WorkspaceIsolationTest(unittest.TestCase):
    def test_dependency_overrides_ambient_output_base_and_exits_its_jvm(self):
        root = Path("/build/swss")
        child = root / ".cargo-bazel-prep" / "common-unique"
        args = argparse.Namespace(workspace=root, bazel="bazel", bazel_arg=[], bazel_startup_arg=[])
        with patch.object(prepare.subprocess, "run") as run:
            prepare.run_bazel(args, child, "fetch", ["--repo=@crates"])
        command = run.call_args.args[0]
        self.assertIn("--output_base=/build/swss/.cargo-bazel-prep/.bazel-output-common-unique", command)
        self.assertIn("--batch", command)
        self.assertEqual(run.call_args.kwargs["cwd"], child)

    def test_explicit_root_output_base_is_preserved_only_for_root(self):
        root = Path("/build/swss")
        child = root / ".cargo-bazel-prep" / "common-unique"
        args = argparse.Namespace(workspace=root, bazel="bazel", bazel_arg=[],
                                  bazel_startup_arg=["--output_base=/cache/root"])
        with patch.object(prepare.subprocess, "run") as run:
            prepare.run_bazel(args, root, "fetch", ["--repo=@crates"])
            root_command = run.call_args.args[0]
            prepare.run_bazel(args, child, "fetch", ["--repo=@crates"])
            child_command = run.call_args.args[0]
        self.assertIn("--output_base=/cache/root", root_command)
        self.assertNotIn("--batch", root_command)
        self.assertNotIn("--output_base=/cache/root", child_command)
        self.assertIn("--batch", child_command)


class PreparationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "Cargo.lock").write_bytes(CARGO)
        (self.root / "MODULE.bazel").write_text('module(name = "app")\n')
        (self.root / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n')
        self.receipt = self.root / "preparation.json"
        self.args = argparse.Namespace(bazel="bazel", bazel_arg=[], bazel_startup_arg=[])

    def generate(self, *args, **kwargs):
        self.assertEqual((self.root / "Cargo.Bazel.lock").read_bytes(), b"")
        self.assertEqual(kwargs["env"]["CARGO_BAZEL_REPIN"], "workspace")
        (self.root / "Cargo.Bazel.lock").write_text(json.dumps(graph()))
        return ["bazel", "fetch", "--repo=@crates"]

    def run_prepare(self):
        return prepare.prepare_one(self.args, self.root, "crates", "Cargo.lock", "Cargo.Bazel.lock", self.receipt)

    def test_clean_checkout_and_stale_cached_lock_are_regenerated(self):
        with patch.object(prepare, "run_bazel", side_effect=self.generate) as run:
            self.run_prepare()
            (self.root / "Cargo.Bazel.lock").write_text("stale cache")
            result = self.run_prepare()
            self.assertEqual(run.call_count, 2)
        self.assertTrue(result["cargo_lock_unchanged"])
        self.assertEqual((self.root / "Cargo.lock").read_bytes(), CARGO)
        self.assertEqual(result["cargo_lock_sha256"], prepare.sha256(CARGO))
        self.assertEqual(json.loads(self.receipt.read_text()), result)

    def test_failed_generator_cannot_leave_a_stale_receipt_or_lock(self):
        (self.root / "Cargo.Bazel.lock").write_text("stale metadata")
        self.receipt.write_text("stale receipt")
        with patch.object(prepare, "run_bazel", side_effect=RuntimeError("generator failed")):
            with self.assertRaisesRegex(RuntimeError, "generator failed"):
                self.run_prepare()
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.root / "Cargo.Bazel.lock").exists())
        self.assertEqual((self.root / "Cargo.lock").read_bytes(), CARGO)

    def test_generated_lock_symlink_cannot_overwrite_another_file(self):
        unrelated = self.root / "retained.json"
        unrelated.write_text("keep")
        (self.root / "Cargo.Bazel.lock").symlink_to(unrelated)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.run_prepare()
        self.assertEqual(unrelated.read_text(), "keep")

    def test_source_and_generated_lock_cannot_be_the_same_file(self):
        with self.assertRaisesRegex(ValueError, "different files"):
            prepare.prepare_one(self.args, self.root, "crates", "Cargo.lock", "Cargo.lock", self.receipt)
        self.assertEqual((self.root / "Cargo.lock").read_bytes(), CARGO)

    def test_dependency_failure_invalidates_old_root_metadata_and_overrides(self):
        generated = self.root / "Cargo.Bazel.lock"
        generated.write_text("stale metadata")
        rc = self.root / ".bazelrc.rust"
        rc.write_text(prepare.RC_MARKER + "common --override_module=common=/old\n")
        self.receipt.write_text("stale receipt")
        def fail_stage(*args):
            # Resolution cannot see the previous override after a source pin update.
            self.assertFalse(rc.exists())
            self.assertFalse(generated.exists())
            self.assertFalse(self.receipt.exists())
            raise RuntimeError("new source cannot be fetched")
        argv = ["prepare.py", "--workspace", str(self.root),
                "--receipt", str(self.receipt), "--dependency=common=common",
                "--staging-dir", str(self.root / "staged"), "--overrides-rc", str(rc)]
        with patch.object(sys, "argv", argv), patch.object(prepare, "stage_dependency", side_effect=fail_stage):
            with self.assertRaisesRegex(RuntimeError, "new source"):
                prepare.main()
        self.assertEqual((self.root / "Cargo.lock").read_bytes(), CARGO)

    def test_source_lock_mutation_is_rejected_and_original_restored(self):
        def change_source(*args, **kwargs):
            self.generate(*args, **kwargs)
            (self.root / "Cargo.lock").write_bytes(CARGO + b"# changed\n")
        with patch.object(prepare, "run_bazel", side_effect=change_source):
            with self.assertRaisesRegex(ValueError, "changed Cargo.lock"):
                self.run_prepare()
        self.assertEqual((self.root / "Cargo.lock").read_bytes(), CARGO)
        self.assertFalse((self.root / "Cargo.Bazel.lock").exists())
        self.assertFalse(self.receipt.exists())


if __name__ == "__main__":
    unittest.main()
