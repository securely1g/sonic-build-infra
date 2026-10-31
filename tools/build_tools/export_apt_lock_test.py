#!/usr/bin/env python3
"""Checks for the version-bound build_tools lock preparation boundary."""

import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_apt_lock as export


def package(name, architecture, version="1"):
    return {
        "name": name,
        "version": version,
        "architecture": architecture,
        "sha256": hashlib.sha256((name + architecture + version).encode()).hexdigest(),
        "filename": "pool/main/" + name[0] + "/" + name + "/" + name + "_" + version + "_" + architecture + ".deb",
        "suite": "trixie",
        "section": "utils",
        "size": 1,
        "depends_on": [],
    }


def resolver_lock():
    packages, sets = {}, {}
    for architecture in sorted(export.ARCHITECTURES):
        tool = "/trixie/tool:" + architecture + "=1"
        library = "/trixie/library:" + architecture + "=1"
        packages[tool] = package("tool", "all")
        packages[tool]["depends_on"] = [library]
        packages[library] = package("library", architecture)
        sets[architecture] = {tool.rsplit("=", 1)[0]: "1"}
    packages["/trixie/unrelated:amd64=1"] = package("unrelated", "amd64")
    return {
        "version": 2,
        "facts": {"index": "sha256-example"},
        "sources": {"trixie": {"architectures": ["amd64", "arm64", "armhf"], "components": ["main"],
                                  "types": [], "uris": ["https://snapshot.example/debian/date"]}},
        "packages": packages,
        "dependency_sets": {"build_tools": {"sets": sets},
                            "other": {"sets": {"amd64": {"/trixie/unrelated:amd64": "1"}}}},
    }


def module_lock(lock, imported=False):
    attributes = {"depset_name": "build_tools", "lock_content": json.dumps(lock)}
    if imported:
        attributes.update(repo_prefix="locked_import_build_tools_", payload_only=True)
    return {
        "lockFileVersion": export.BAZEL_LOCK_VERSION,
        "registryFileHashes": dict(export.RULES_REGISTRY_HASHES),
        "moduleExtensions": {export.APT_EXTENSION: {"general": {"generatedRepoSpecs": {
            "build_tools": {"repoRuleId": export.HUB_RULE, "attributes": attributes},
        }}}},
    }


def imported_module(lock):
    sets, selected = {}, set()
    for architecture in sorted(export.ARCHITECTURES):
        keys = export.package_keys(lock, "build_tools", architecture)
        sets[architecture] = {key.rsplit("=", 1)[0]: lock["packages"][key]["version"] for key in keys}
        selected.update(keys)
    imported = dict(lock, dependency_sets={"build_tools": {"sets": sets}}, packages={
        key: dict(lock["packages"][key], depends_on=sorted(export.reachable(lock["packages"], [key]) - {key}))
        for key in selected
    })
    result = module_lock(imported, imported=True)
    repositories = result["moduleExtensions"][export.APT_EXTENSION]["general"]["generatedRepoSpecs"]
    for key in sorted(selected):
        value = lock["packages"][key]
        name = export.package_repository(key)
        repositories[name] = {
            "repoRuleId": export.DEB_RULE,
            "attributes": {"target_name": name, "repo_prefix": "locked_import_build_tools_", "payload_only": True, "mergedusr": False,
                           "package_name": value["name"], "sha256": value["sha256"],
                           "urls": export.package_urls(lock, value),
                           "depends_on": sorted(export.reachable(lock["packages"], [key]) - {key})},
        }
    return result


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.resolved = resolver_lock()
        self.module = module_lock(self.resolved)

    def test_projects_only_the_complete_shared_graph(self):
        lock = export.project_candidate(self.module)
        self.assertEqual(set(lock["dependency_sets"]), {"build_tools"})
        self.assertEqual(set(lock["dependency_sets"]["build_tools"]["sets"]), export.ARCHITECTURES)
        self.assertEqual(len(lock["packages"]), 6)
        self.assertNotIn("/trixie/unrelated:amd64=1", lock["packages"])
        self.assertNotIn("facts", lock)
        self.assertEqual(lock["sources"], self.resolved["sources"])
        self.assertIn("Bazel 8.5.1", lock["_generated"])

    def test_rejects_unreviewed_bazel_and_registry_records(self):
        wrong_version = copy.deepcopy(self.module)
        wrong_version["lockFileVersion"] += 1
        with self.assertRaisesRegex(ValueError, "version 24"):
            export.project_candidate(wrong_version)
        wrong_registry = copy.deepcopy(self.module)
        wrong_registry["registryFileHashes"][next(iter(export.RULES_REGISTRY_HASHES))] = "0" * 64
        with self.assertRaisesRegex(ValueError, "reviewed rules_distroless"):
            export.project_candidate(wrong_registry)
        imported = copy.deepcopy(self.module)
        imported["moduleExtensions"][export.APT_EXTENSION]["general"]["generatedRepoSpecs"]["build_tools"]["attributes"]["payload_only"] = True
        with self.assertRaisesRegex(ValueError, "repository attributes"):
            export.project_candidate(imported)

    def test_rejects_changed_resolver_shape(self):
        self.resolved["new_unreviewed_field"] = {}
        with self.assertRaisesRegex(ValueError, "lock_content shape"):
            export.project_candidate(module_lock(self.resolved))

    def test_rejects_missing_foreign_and_duplicate_package_graphs(self):
        for kind in ("missing", "foreign", "duplicate"):
            resolved = copy.deepcopy(self.resolved)
            root = resolved["packages"]["/trixie/tool:amd64=1"]
            if kind == "missing":
                root["depends_on"].append("/trixie/missing:amd64=1")
            elif kind == "foreign":
                resolved["packages"]["/trixie/library:amd64=1"]["architecture"] = "arm64"
            else:
                resolved["packages"]["/trixie/library:amd64=2"] = package("library", "amd64", "2")
                root["depends_on"].append("/trixie/library:amd64=2")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                export.project_candidate(module_lock(resolved))

    def test_rejects_unsafe_archive_metadata(self):
        for field, value in (("sha256", "0" * 63), ("filename", "../package.deb"), ("filename", "pool/package.tar")):
            resolved = copy.deepcopy(self.resolved)
            resolved["packages"]["/trixie/tool:amd64=1"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                export.project_candidate(module_lock(resolved))
        self.resolved["sources"]["trixie"]["uris"] = ["https://user:secret@snapshot.example/debian"]
        with self.assertRaisesRegex(ValueError, "source URI"):
            export.project_candidate(module_lock(self.resolved))

    def test_bazel_executable_version_is_checked(self):
        with patch("export_apt_lock.shutil.which", return_value="/usr/bin/bazel"), \
             patch("export_apt_lock.subprocess.run", return_value=SimpleNamespace(stdout="bazel 9.0.0\n")):
            with self.assertRaisesRegex(ValueError, "requires Bazel 8.5.1"):
                export.check_bazel("bazel")


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.lock = export.project_candidate(module_lock(resolver_lock()))
        self.module = imported_module(self.lock)

    def test_accepts_the_upstream_expanded_graph_and_archives(self):
        counts = export.validate_import(self.lock, self.module)
        self.assertEqual(counts, {architecture: {"roots": 1, "packages": 2} for architecture in export.ARCHITECTURES})

    def test_rejects_missing_expanded_package(self):
        attributes = self.module["moduleExtensions"][export.APT_EXTENSION]["general"]["generatedRepoSpecs"]["build_tools"]["attributes"]
        imported = json.loads(attributes["lock_content"])
        del imported["dependency_sets"]["build_tools"]["sets"]["amd64"]["/trixie/library:amd64"]
        attributes["lock_content"] = json.dumps(imported)
        with self.assertRaisesRegex(ValueError, "dependency sets"):
            export.validate_import(self.lock, self.module)

    def test_rejects_changed_original_archive(self):
        repositories = self.module["moduleExtensions"][export.APT_EXTENSION]["general"]["generatedRepoSpecs"]
        name = export.package_repository("/trixie/library:amd64=1")
        repositories[name]["attributes"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "original DEB"):
            export.validate_import(self.lock, self.module)

    def test_rejects_archive_attributes_swapped_between_architectures(self):
        repositories = self.module["moduleExtensions"][export.APT_EXTENSION]["general"]["generatedRepoSpecs"]
        amd64 = export.package_repository("/trixie/library:amd64=1")
        arm64 = export.package_repository("/trixie/library:arm64=1")
        repositories[amd64]["attributes"], repositories[arm64]["attributes"] = \
            repositories[arm64]["attributes"], repositories[amd64]["attributes"]
        with self.assertRaisesRegex(ValueError, "original DEB"):
            export.validate_import(self.lock, self.module)

    def test_rejects_resolver_consumption_in_an_ordinary_build(self):
        with self.assertRaisesRegex(ValueError, "repository attributes"):
            export.validate_import(self.lock, module_lock(resolver_lock()))


if __name__ == "__main__":
    unittest.main()
