#!/usr/bin/env python3
"""Query a real metadata repository from a cold workspace, then change its lock.

Run directly with --bazel PATH. Queries evaluate only our repository rule and
native filegroups; this test performs no build actions or package downloads.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

APT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APT))
from sonic_apt.inputs import declarations, subset_inputs


def package(name, architecture, version, dependencies=()):
    return {
        "name": name, "architecture": architecture, "version": version, "suite": "trixie",
        "filename": "pool/" + name + "_" + version + "_" + architecture + ".deb",
        "sha256": hashlib.sha256((name + ":" + architecture + "=" + version).encode()).hexdigest(),
        "depends_on": list(dependencies),
    }


def fixture():
    packages = {
        "/trixie/data:all=1": package("data", "all", "1"),
        "/trixie/dictionary:all=1": package("dictionary", "all", "1", ["/trixie/data:all=1"]),
    }
    sets = {}
    for arch in ("amd64", "arm64"):
        key = "/trixie/app:" + arch
        independent = "/trixie/independent:" + arch
        sets[arch] = {key: "1", independent: "2"}
        packages[key + "=1"] = package("app", arch, "1", ["/trixie/dictionary:all=1"])
        packages[independent + "=2"] = package("independent", arch, "2")
    return {"version": 2, "sources": {"trixie": {"uris": ["https://example.test/debian/", "http://mirror.example.test/debian"]}},
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
                actual = json.loads(generated.read_text().split("APT_INPUTS = ", 1)[1])
                expected = json.loads(declarations(lock)[1].split("APT_INPUTS = ", 1)[1])
                self.assertEqual(actual, expected)
                self.assertFalse(generated.with_name("apt_manifest.json").exists())
                self.assertIn('exports_files(["apt_inputs.bzl"], visibility = ["//visibility:public"])',
                              generated.with_name("BUILD.bazel").read_text())
                return actual

            lock = fixture()
            original = query(lock)
            key = "/trixie/new-dependency:all=2"
            lock["packages"][key] = package("new-dependency", "all", "2", ["/trixie/dictionary:all=1"])
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

    def test_subset_generation_manifest_and_invalid_requests(self):
        with tempfile.TemporaryDirectory(prefix="apt-subset-inputs-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            shutil.copyfile(APT / "inputs_repository.bzl", workspace / "inputs_repository.bzl")
            (workspace / "BUILD.bazel").write_text(
                'load("@subset_inputs//:apt_inputs.bzl", "APT_INPUTS")\n'
                'exports_files(["apt.lock.json", "inputs_repository.bzl"])\n'
                'filegroup(name = "metadata", srcs = ["@subset_inputs//:apt_inputs.bzl", "@subset_inputs//:apt_manifest.json"])\n')
            command = [BAZEL, "--batch", "--ignore_all_rc_files", "--output_base=" + str(root / "output"),
                       "query", "--lockfile_mode=off", "--repository_disable_download",
                       "--incompatible_autoload_externally=", "--repository_cache=" + str(root / "cache"),
                       "--output=location", "//:metadata + @subset_inputs//:apt_manifest.json"]

            def query(lock, roots, error=None, group="image_apt", architecture="amd64"):
                (workspace / "apt.lock.json").write_text(json.dumps(lock))
                (workspace / "MODULE.bazel").write_text(
                    'module(name = "apt_inputs_test")\n'
                    'apt_inputs = use_repo_rule("//:inputs_repository.bzl", "apt_inputs")\n'
                    'apt_inputs(name = "subset_inputs", lock = "//:apt.lock.json", dependency_set = ' + json.dumps(group) +
                    ', architecture = ' + json.dumps(architecture) + ', root_packages = ' + json.dumps(roots) + ')\n')
                result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=60)
                if error:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(error, result.stderr)
                    return
                self.assertEqual(result.returncode, 0, result.stderr)
                generated = next((root / "output/external").glob("*subset_inputs/apt_inputs.bzl"))
                actual = json.loads(generated.read_text().split("APT_INPUTS = ", 1)[1])
                manifest = json.loads(generated.with_name("apt_manifest.json").read_text())
                expected, expected_manifest = subset_inputs(lock, group, architecture, roots)
                self.assertEqual(actual, expected)
                self.assertEqual(manifest, expected_manifest)
                return actual, manifest

            lock = fixture()
            inputs, manifest = query(lock, ["dictionary"])
            selected = {"/trixie/data:all=1", "/trixie/dictionary:all=1"}
            self.assertEqual(set(inputs["image_apt"]["amd64"]), selected)
            self.assertEqual(set(manifest), {"schema", "dependency_set", "architecture", "root_packages", "packages"})
            self.assertEqual(set(manifest["packages"]), selected)
            self.assertEqual(manifest["packages"]["/trixie/dictionary:all=1"]["depends_on"], ["/trixie/data:all=1"])
            self.assertEqual(manifest["packages"]["/trixie/dictionary:all=1"]["urls"], [
                "https://example.test/debian/pool/dictionary_1_all.deb",
                "http://mirror.example.test/debian/pool/dictionary_1_all.deb",
            ])
            for roots, error in [([], "requires dependency_set"), (["missing"], "root is not in dependency set"),
                                 (["dictionary", "dictionary"], "unique package names")]:
                with self.subTest(roots=roots):
                    query(lock, roots, error=error)
            invalid = copy.deepcopy(lock)
            invalid["packages"]["/trixie/independent:amd64=2"]["sha256"] = "not-a-digest"
            query(invalid, ["dictionary"], error="invalid SHA256")
            self.assertEqual(query(lock, ["dictionary"]), (inputs, manifest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    args, remaining = parser.parse_known_args()
    BAZEL = str(Path(shutil.which(args.bazel) or args.bazel).resolve())
    unittest.main(argv=[sys.argv[0], *remaining])
