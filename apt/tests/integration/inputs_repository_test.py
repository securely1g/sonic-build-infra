#!/usr/bin/env python3
"""Query a real metadata repository from a cold workspace, then change its lock.

Run directly with --bazel PATH. Queries evaluate only our repository rule and
native filegroups; this test performs no build actions or package downloads.
"""

import argparse
import ast
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

APT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APT))
from sonic_apt.inputs import declarations


def fixture():
    packages = {
        "/trixie/dictionary:all=1": {
            "name": "dictionary", "architecture": "all", "version": "1", "depends_on": [],
        },
    }
    sets = {}
    for arch in ("amd64", "arm64"):
        key = "/trixie/app:" + arch
        sets[arch] = {key: "1"}
        packages[key + "=1"] = {
            "name": "app", "architecture": arch, "version": "1",
            "depends_on": ["/trixie/dictionary:all=1"],
        }
    return {"version": 2, "sources": {"trixie": {}},
            "dependency_sets": {"image_apt": {"sets": sets}}, "packages": packages}


class InputsRepositoryTest(unittest.TestCase):
    def test_cold_generation_lock_invalidation_and_invalid_locks(self):
        with tempfile.TemporaryDirectory(prefix="apt-inputs-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            shutil.copyfile(APT / "inputs_repository.bzl", workspace / "inputs_repository.bzl")
            (workspace / "MODULE.bazel").write_text(
                'module(name = "apt_inputs_test")\n'
                'apt_inputs = use_repo_rule("//:inputs_repository.bzl", "apt_inputs")\n'
                'apt_inputs(name = "locked_inputs", lock = "//:apt.lock.json")\n')
            (workspace / "BUILD.bazel").write_text(
                'load("@locked_inputs//:apt_inputs.bzl", "APT_INPUTS")\n'
                'exports_files(["apt.lock.json", "inputs_repository.bzl"])\n'
                'filegroup(name = "metadata", srcs = ["@locked_inputs//:apt_inputs.bzl"])\n')
            # Keep this metadata-only probe independent of cached language rules.
            command = [BAZEL, "--batch", "--ignore_all_rc_files", "--output_base=" + str(root / "output"),
                       "query", "--lockfile_mode=off", "--repository_disable_download",
                       "--incompatible_autoload_externally=", "--repository_cache=" + str(root / "cache"),
                       "--output=location",
                       "//:metadata + @locked_inputs//:apt_inputs.bzl"]

            def query(lock, error=None):
                (workspace / "apt.lock.json").write_text(json.dumps(lock))
                result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=60)
                if error:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(error, result.stderr)
                    return
                self.assertEqual(result.returncode, 0, result.stderr)
                generated = next((root / "output/external").glob("*/apt_inputs.bzl"))
                actual = ast.literal_eval(generated.read_text().split("APT_INPUTS = ", 1)[1])
                expected = ast.literal_eval(declarations(lock)[1].split("APT_INPUTS = ", 1)[1])
                self.assertEqual(actual, expected)
                return actual

            lock = fixture()
            original = query(lock)
            key = "/trixie/new-dependency:all=2"
            lock["packages"][key] = {"name": "new-dependency", "architecture": "all", "version": "2",
                                     "depends_on": ["/trixie/dictionary:all=1"]}
            # Add a dependency and a cycle without changing the MODULE file.
            lock["packages"]["/trixie/dictionary:all=1"]["depends_on"] = [key]
            updated = query(lock)
            self.assertNotEqual(original, updated)
            for arch in ("amd64", "arm64"):
                self.assertEqual(updated["image_apt"][arch][key], "@image_apt//new-dependency")

            invalid_cases = []
            missing = copy.deepcopy(lock)
            del missing["packages"][key]
            invalid_cases.append((missing, "dependency is missing"))
            identity = copy.deepcopy(lock)
            identity["packages"][key]["name"] = "another-name"
            invalid_cases.append((identity, "does not match its identity"))
            foreign = copy.deepcopy(lock)
            foreign["packages"][key]["architecture"] = "arm64"
            invalid_cases.append((foreign, "foreign architecture"))
            duplicate = copy.deepcopy(lock)
            other = key.replace("=2", "=3")
            duplicate["packages"][other] = {**duplicate["packages"][key], "version": "3"}
            duplicate["packages"][key]["depends_on"].append(other)
            invalid_cases.append((duplicate, "one public package target"))
            for value, message in invalid_cases:
                with self.subTest(error=message):
                    query(value, error=message)
            # A valid lock also recovers after a failed repository evaluation.
            self.assertEqual(query(lock), updated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    args, remaining = parser.parse_known_args()
    BAZEL = str(Path(shutil.which(args.bazel) or args.bazel).resolve())
    unittest.main(argv=[sys.argv[0], *remaining])
