"""Check the reviewed package declarations and selected archive handoff."""

import copy
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from sonic_apt.inputs import declarations, package_keys, subset_inputs


class InputsTest(unittest.TestCase):
    @staticmethod
    def package(name, version, architecture, dependencies=()):
        return {
            "name": name,
            "version": version,
            "architecture": architecture,
            "suite": "trixie",
            "filename": "pool/" + name + "_" + version + "_" + architecture + ".deb",
            "sha256": hashlib.sha256((name + ":" + architecture + "=" + version).encode()).hexdigest(),
            "depends_on": list(dependencies),
        }

    def setUp(self):
        self.base = "/trixie/libssl:amd64=3.5.7+fips"
        self.app = "/trixie/tcpdump:amd64=4.99.5"
        self.lock = {
            "version": 2,
            "sources": {"trixie": {"uris": ["https://example.test/debian/", "http://mirror.example.test/debian"]}},
            "dependency_sets": {"image_apt": {"sets": {"amd64": {self.app.rsplit("=", 1)[0]: "4.99.5"}}}},
            "packages": {
                self.base: self.package("libssl", "3.5.7+fips", "amd64"),
                self.app: self.package("tcpdump", "4.99.5", "amd64", [self.base]),
            },
        }

    def subset_lock(self):
        lock = copy.deepcopy(self.lock)
        leaf = "/trixie/runtime-data:all=1"
        independent = "/trixie/independent:amd64=2"
        lock["packages"][leaf] = self.package("runtime-data", "1", "all")
        lock["packages"][self.base]["depends_on"] = [leaf]
        lock["packages"][independent] = self.package("independent", "2", "amd64")
        lock["dependency_sets"]["image_apt"]["sets"]["amd64"][independent.rsplit("=", 1)[0]] = "2"
        return lock, leaf, independent

    def test_export_keeps_complete_candidates_including_base_dependencies(self):
        module, bzl = declarations(self.lock)
        self.assertIn('"libssl (= 3.5.7+fips) [amd64]"', module)
        self.assertIn('"tcpdump (= 4.99.5) [amd64]"', module)
        self.assertIn('"@image_apt//libssl"', bzl)
        self.assertNotIn("from_lock", module)
        self.assertEqual(package_keys(self.lock, "image_apt", "amd64"), sorted([self.app, self.base]))

    def test_export_is_independent_of_json_key_order(self):
        reordered = json.loads(json.dumps(self.lock, sort_keys=True))
        self.assertEqual(declarations(self.lock), declarations(reordered))

    def test_cycle_is_visited_once(self):
        self.lock["packages"][self.base]["depends_on"] = [self.app]
        self.assertEqual(len(package_keys(self.lock, "image_apt", "amd64")), 2)

    def test_missing_dependency_is_rejected(self):
        del self.lock["packages"][self.base]
        with self.assertRaisesRegex(ValueError, "dependency is missing"):
            declarations(self.lock)

    def test_foreign_architecture_is_rejected(self):
        self.lock["packages"][self.base]["architecture"] = "arm64"
        with self.assertRaisesRegex(ValueError, "foreign architecture"):
            declarations(self.lock)

    def test_package_key_must_match_reviewed_identity(self):
        self.lock["packages"][self.base]["name"] = "another-package"
        with self.assertRaisesRegex(ValueError, "does not match its identity"):
            declarations(self.lock)

    def test_conflicting_public_package_identity_is_rejected(self):
        other = self.base.replace("3.5.7+fips", "3.5.6")
        self.lock["packages"][other] = {**self.lock["packages"][self.base], "version": "3.5.6"}
        self.lock["packages"][self.app]["depends_on"].append(other)
        with self.assertRaisesRegex(ValueError, "one public package target"):
            declarations(self.lock)

    def test_subset_accepts_transitive_seed_and_excludes_other_roots(self):
        lock, leaf, independent = self.subset_lock()
        # Duplicate hashes outside the manifest do not make selected rows ambiguous.
        lock["packages"][independent]["sha256"] = lock["packages"][self.base]["sha256"]
        original = copy.deepcopy(lock)
        default = declarations(lock)
        inputs, manifest = subset_inputs(lock, "image_apt", "amd64", ["runtime-data", "libssl"])
        self.assertEqual(inputs, {"image_apt": {"amd64": {
            self.base: "@image_apt//libssl", leaf: "@image_apt//runtime-data",
        }}})
        self.assertNotIn(self.app, manifest["packages"])
        self.assertNotIn(independent, manifest["packages"])
        self.assertEqual(set(manifest), {"schema", "dependency_set", "architecture", "root_packages", "packages"})
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["dependency_set"], "image_apt")
        self.assertEqual(manifest["architecture"], "amd64")
        self.assertEqual(manifest["root_packages"], ["libssl", "runtime-data"])
        self.assertEqual(list(manifest["packages"]), sorted([self.base, leaf]))
        self.assertEqual(manifest["packages"][self.base], {
            "name": "libssl", "version": "3.5.7+fips", "architecture": "amd64",
            "urls": ["https://example.test/debian/pool/libssl_3.5.7+fips_amd64.deb",
                     "http://mirror.example.test/debian/pool/libssl_3.5.7+fips_amd64.deb"],
            "sha256": lock["packages"][self.base]["sha256"], "depends_on": [leaf],
        })
        self.assertEqual(manifest["packages"][leaf]["architecture"], "all")
        self.assertTrue(all(dependency in manifest["packages"] for row in manifest["packages"].values()
                            for dependency in row["depends_on"]))
        self.assertEqual(lock, original)
        self.assertEqual(declarations(lock), default)

    def test_subset_rejects_missing_malformed_and_duplicate_selection(self):
        lock, _, _ = self.subset_lock()
        cases = [
            (("", "amd64", ["libssl"]), "requires dependency_set"),
            (("image_apt", "", ["libssl"]), "requires dependency_set"),
            (("image_apt", "amd64", []), "requires dependency_set"),
            (("missing", "amd64", ["libssl"]), "lacks dependency set"),
            (("image_apt", "arm64", ["libssl"]), "lacks architecture"),
            (("image_apt", "amd64", ["missing"]), "root is not in dependency set"),
            (("image_apt", "amd64", ["Bad/Name"]), "valid package names"),
            (("image_apt", "amd64", ["libssl", "libssl"]), "unique package names"),
        ]
        for arguments, error in cases:
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, error):
                subset_inputs(lock, *arguments)

    def test_subset_validates_excluded_independent_closure(self):
        lock, _, independent = self.subset_lock()
        lock["packages"][independent]["depends_on"] = ["/trixie/missing:amd64=1"]
        with self.assertRaisesRegex(ValueError, "dependency is missing"):
            subset_inputs(lock, "image_apt", "amd64", ["libssl"])

    def test_subset_rejects_invalid_manifest_metadata(self):
        lock, leaf, _ = self.subset_lock()
        cases = [
            ("version", "not-a-version", "does not match its identity"),
            ("suite", "another", "does not match its identity"),
            ("architecture", "arm64", "foreign architecture"),
            ("sha256", "A" * 64, "invalid SHA256"),
            ("filename", "../outside.deb", "invalid APT package filename"),
            ("depends_on", "not-a-list", "lacks dependency graph"),
        ]
        for field, value, error in cases:
            invalid = copy.deepcopy(lock)
            invalid["packages"][self.base][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                subset_inputs(invalid, "image_apt", "amd64", ["libssl"])
        unsafe_name = copy.deepcopy(lock)
        unsafe_key = self.base.replace("/libssl:", "/Bad:")
        unsafe_name["packages"][unsafe_key] = unsafe_name["packages"].pop(self.base)
        unsafe_name["packages"][unsafe_key]["name"] = "Bad"
        unsafe_name["packages"][self.app]["depends_on"] = [unsafe_key]
        with self.assertRaisesRegex(ValueError, "invalid APT package name"):
            subset_inputs(unsafe_name, "image_apt", "amd64", ["Bad"])
        for uri in ("file:///tmp/packages", "https://", "https://example.test/a/../b",
                    "https://example.test/%2e%2e/b", "https://user:secret@example.test/debian", "https://example.test/\n"):
            invalid = copy.deepcopy(lock)
            invalid["sources"]["trixie"]["uris"] = [uri]
            with self.subTest(uri=uri), self.assertRaisesRegex(ValueError, "invalid APT source URL"):
                subset_inputs(invalid, "image_apt", "amd64", ["libssl"])
        duplicate = copy.deepcopy(lock)
        duplicate["packages"][leaf]["sha256"] = duplicate["packages"][self.base]["sha256"]
        with self.assertRaisesRegex(ValueError, "duplicate SHA256"):
            subset_inputs(duplicate, "image_apt", "amd64", ["libssl"])

    def test_selected_archives_are_copied_intact_in_order(self):
        from sonic_apt.selection import stage_payloads

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.tar", root / "second.tar"
            first.write_bytes(b"first archive bytes")
            second.write_bytes(b"second archive bytes")
            stage_payloads([second, first], root / "selected")
            self.assertEqual((root / "selected/000001.tar").read_bytes(), second.read_bytes())
            self.assertEqual((root / "selected/000002.tar").read_bytes(), first.read_bytes())
            with tarfile.open(root / "selected/000000-empty.tar") as archive:
                self.assertEqual(archive.getmembers(), [])

    def test_empty_selection_still_supplies_a_valid_empty_archive(self):
        from sonic_apt.selection import stage_payloads

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "selected"
            stage_payloads([], target)
            self.assertEqual([p.name for p in target.iterdir()], ["000000-empty.tar"])
            with tarfile.open(target / "000000-empty.tar") as archive:
                self.assertEqual(archive.getmembers(), [])
            with self.assertRaisesRegex(ValueError, "must be empty"):
                stage_payloads([], target)


if __name__ == "__main__":
    unittest.main()
