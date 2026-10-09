"""Check the reviewed package declarations and selected archive handoff."""

import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from sonic_apt.inputs import declarations, package_keys
from sonic_apt.selection import stage_payloads


class InputsTest(unittest.TestCase):
    def setUp(self):
        self.base = "/trixie/libssl:amd64=3.5.7+fips"
        self.app = "/trixie/tcpdump:amd64=4.99.5"
        self.lock = {
            "sources": {"trixie": {}},
            "dependency_sets": {"image_apt": {"sets": {"amd64": {self.app.rsplit("=", 1)[0]: "4.99.5"}}}},
            "packages": {
                self.base: {"name": "libssl", "version": "3.5.7+fips", "architecture": "amd64", "depends_on": []},
                self.app: {"name": "tcpdump", "version": "4.99.5", "architecture": "amd64", "depends_on": [self.base]},
            },
        }

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
        with self.assertRaises(KeyError):
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

    def test_selected_archives_are_copied_intact_in_order(self):
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
