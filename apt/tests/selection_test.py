#!/usr/bin/env python3
"""Check package retention and caller-owned inventories using tar/JSON fixtures."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock

from sonic_apt import selection as subject


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_tar(path, entries):
    with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as archive:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.mode, member.size = 0o644, len(data)
            archive.addfile(member, io.BytesIO(data))


class SelectionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="apt-selection-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = {"version": 2, "dependency_sets": {}, "packages": {}}
        self.mapping = {"architecture": "arm64", "dependency_set": "routing", "locked": []}
        self.paths, self.controls, self.inventories = {}, {}, {}
        self.elf = {"kind": "file", "sha256": "e" * 64, "elf_machine": 183}
        self.data = {"kind": "file", "sha256": "f" * 64}
        self.base = {"usr/lib/libssl.so.3": self.elf, "etc/router": self.data}
        self.installed = {"libssl": "3.5.7+fips"}
        self.retained = {"local-net": {"source_sha256": "d" * 64}}
        self.add_package("libssl", {"usr/lib/libssl.so.3": {**self.elf, "sha256": "0" * 64}})
        self.add_package("local-net", {"usr/lib/local.so": self.elf})
        self.add_package("router", {"usr/share/router/data": self.data})
        self.inspect = Mock(side_effect=lambda path: self.inventories[path])
        self.overlay = Mock()

    def add_package(self, name, inventory, *, suite="trixie"):
        key = "/" + suite + "/" + name + ":arm64=1.0"
        payload = self.root / (suite + "-" + name + ".tar")
        control = payload.with_suffix(".control.tar")
        write_tar(payload, [("usr/share/" + name, name.encode())])
        write_tar(control, [("control", ("Package: " + name + "\nVersion: 1.0\n").encode())])
        self.content["packages"][key] = {
            "name": name, "version": "1.0", "architecture": "arm64", "suite": suite,
            "filename": "pool/" + name + ".deb", "sha256": digest(name.encode()), "size": 42,
            "depends_on": [], "payload_sha256": digest(payload.read_bytes()),
            "payload_size": payload.stat().st_size, "control_sha256": digest(control.read_bytes()),
            "control_size": control.stat().st_size,
        }
        self.mapping["locked"].append({"key": key, "package": self.content["packages"][key],
                                       "payload": str(payload), "control": str(control)})
        self.paths[name], self.controls[name] = payload, control
        self.inventories[payload] = inventory
        return key

    def select(self):
        lock_path, mapping_path = self.root / "lock.json", self.root / "mapping.json"
        lock_path.write_text(json.dumps(self.content))
        mapping_path.write_text(json.dumps(self.mapping))
        return subject.select(lock_path, mapping_path, group="routing", architecture="arm64",
                              installed=self.installed, base_files=self.base,
                              retained_packages=self.retained, inspect_payload=self.inspect,
                              check_overlay=self.overlay)

    def test_base_and_explicitly_retained_packages_keep_their_existing_content(self):
        paths, receipt = self.select()
        self.assertEqual(paths, [self.paths["router"]])
        self.assertEqual(receipt["group"], "routing")
        self.assertEqual(receipt["skipped_base"][0]["base_version"], "3.5.7+fips")
        self.assertEqual(receipt["skipped_retained"][0]["source_sha256"], "d" * 64)
        self.inspect.assert_called_once_with(self.paths["router"])
        self.overlay.assert_called_once_with(self.inventories[self.paths["router"]], self.base)

    def test_identical_sources_are_deduplicated_before_inspection(self):
        kept = self.paths["router"]
        key = self.add_package("router", self.inventories[kept], suite="trixie-security")
        paths, receipt = self.select()
        self.assertEqual(len(paths), 1)
        self.assertIn(paths[0], (kept, self.paths["router"]))
        duplicate = receipt["duplicate_sources"]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual({duplicate[0]["kept_key"], duplicate[0]["duplicate_key"]},
                         {"/trixie/router:arm64=1.0", key})
        self.inspect.assert_called_once_with(paths[0])

    def test_same_size_payload_and_control_tampering_is_rejected_before_callbacks(self):
        for kind, path in (("payload", self.paths["libssl"]), ("control", self.controls["router"])):
            with self.subTest(kind=kind):
                original = path.read_bytes()
                path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                with self.assertRaisesRegex(ValueError, "changed locked APT " + kind):
                    self.select()
                path.write_bytes(original)
        self.inspect.assert_not_called()
        self.overlay.assert_not_called()

    def test_foreign_package_set_is_rejected_before_reading_payloads(self):
        self.mapping["architecture"] = "amd64"
        with self.assertRaisesRegex(ValueError, "foreign target architecture"):
            self.select()
        self.inspect.assert_not_called()

    def test_duplicate_package_mapping_is_rejected(self):
        self.mapping["locked"].append(copy.deepcopy(self.mapping["locked"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate package"):
            self.select()

    def test_receipt_records_actual_hashes_when_no_extracted_hashes_are_reviewed(self):
        package = self.content["packages"]["/trixie/router:arm64=1.0"]
        for field in ("payload_sha256", "payload_size", "control_sha256", "control_size"):
            del package[field]
        _, receipt = self.select()
        self.assertEqual(receipt["selected"][0]["payload_sha256"], digest(self.paths["router"].read_bytes()))
        self.assertEqual(receipt["selected"][0]["control_sha256"], digest(self.controls["router"].read_bytes()))

    def test_new_package_cannot_replace_a_base_elf(self):
        self.inventories[self.paths["router"]] = {
            "usr/lib/libssl.so.3": {**self.elf, "sha256": "0" * 64}}
        with self.assertRaisesRegex(ValueError, "replace a base ELF"):
            self.select()
        self.overlay.assert_not_called()

    def test_new_package_cannot_replace_an_elf_selected_from_another_package(self):
        self.inventories[self.paths["router"]] = {"usr/lib/shared.so": self.elf}
        self.add_package("z-router-tools", {"usr/lib/shared.so": {**self.elf, "sha256": "0" * 64}})
        with self.assertRaisesRegex(ValueError, "replace a selected ELF"):
            self.select()

    def test_identical_elf_is_allowed_and_non_elf_changes_are_reported(self):
        self.inventories[self.paths["router"]] = {
            "usr/lib/libssl.so.3": self.elf, "etc/router": {**self.data, "sha256": "0" * 64}}
        paths, receipt = self.select()
        self.assertEqual(paths, [self.paths["router"]])
        self.assertEqual(receipt["changed_non_elf_base_paths"], [{"package": "router", "path": "etc/router"}])

    def test_overlay_callback_receives_combined_selection_and_can_reject_it(self):
        second = {"usr/bin/router-tool": self.elf}
        self.add_package("router-tools", second)
        self.overlay.side_effect = ValueError("consumer rejects directory symlink traversal")
        with self.assertRaisesRegex(ValueError, "consumer rejects directory symlink traversal"):
            self.select()
        self.overlay.assert_called_once_with({**self.inventories[self.paths["router"]], **second}, self.base)

    def test_unsafe_payload_rejection_from_consumer_is_preserved(self):
        self.inspect.side_effect = ValueError("consumer rejects unsafe archive member")
        with self.assertRaisesRegex(ValueError, "consumer rejects unsafe archive member"):
            self.select()
        self.overlay.assert_not_called()

    def test_empty_selection_still_invokes_overlay_check(self):
        self.installed["router"] = "1.0"
        paths, receipt = self.select()
        self.assertEqual(paths, [])
        self.assertEqual(receipt["selected"], [])
        self.inspect.assert_not_called()
        self.overlay.assert_called_once_with({}, self.base)

    def test_base_status_supports_target_and_all_packages_but_rejects_foreign_arch(self):
        path = self.root / "base.tar"
        status = ("Package: router\nVersion: 1.0\nArchitecture: arm64\nStatus: install ok installed\n\n"
                  "Package: docs\nVersion: 2.0\nArchitecture: all\nStatus: install ok installed\n\n"
                  "Package: removed\nVersion: 1\nArchitecture: arm64\nStatus: deinstall ok config-files\n")
        write_tar(path, [("var/lib/dpkg/status", status.encode())])
        self.assertEqual(subject.base_packages([path], architecture="arm64"), {"router": "1.0", "docs": "2.0"})
        with self.assertRaisesRegex(ValueError, "foreign installed package"):
            subject.base_packages([path], architecture="amd64")

    def test_base_status_whiteout_removes_lower_file_but_preserves_same_layer_replacement(self):
        base, update = self.root / "base.tar", self.root / "update.tar"
        status = b"Package: router\nVersion: 1\nArchitecture: arm64\nStatus: install ok installed\n"
        write_tar(base, [("var/lib/dpkg/status", status)])
        for marker in ("var/lib/dpkg/.wh.status", "var/lib/dpkg/.wh..wh..opq"):
            with self.subTest(marker=marker):
                write_tar(update, [(marker, b"")])
                with self.assertRaisesRegex(ValueError, "lacks dpkg status"):
                    subject.base_packages([base, update], architecture="arm64")
                write_tar(update, [("var/lib/dpkg/status", status.replace(b"Version: 1", b"Version: 2")), (marker, b"")])
                self.assertEqual(subject.base_packages([base, update], architecture="arm64"), {"router": "2"})


if __name__ == "__main__":
    unittest.main()
