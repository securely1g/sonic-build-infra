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

from sonic_apt import dependencies
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
        self.content = {"version": 2, "dependency_sets": {"routing": {"sets": {"arm64": {}}}}, "packages": {}}
        self.mapping = {"architecture": "arm64", "dependency_set": "routing", "locked": []}
        self.paths, self.controls, self.inventories = {}, {}, {}
        self.elf = {"kind": "file", "sha256": "e" * 64, "elf_machine": 183}
        self.data = {"kind": "file", "sha256": "f" * 64}
        self.base = {"usr/lib/libssl.so.3": self.elf, "etc/router": self.data}
        self.installed = {"libssl": dependencies.Package("libssl", "3.5.7+fips", "arm64", origin="OCI base")}
        self.retained = {"local-net": {"source_sha256": "d" * 64, "control": {
            "Package": "local-net", "Version": "2.0", "Architecture": "arm64"}}}
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
        write_tar(control, [("control", ("Package: " + name + "\nVersion: 1.0\nArchitecture: arm64\n").encode())])
        self.content["packages"][key] = {
            "name": name, "version": "1.0", "architecture": "arm64", "suite": suite,
            "filename": "pool/" + name + ".deb", "sha256": digest(name.encode()), "size": 42,
            "depends_on": [], "payload_sha256": digest(payload.read_bytes()),
            "payload_size": payload.stat().st_size, "control_sha256": digest(control.read_bytes()),
            "control_size": control.stat().st_size,
        }
        short_key, version = key.rsplit("=", 1)
        self.content["dependency_sets"]["routing"]["sets"]["arm64"][short_key] = version
        self.mapping["locked"].append({"key": key, "package": self.content["packages"][key],
                                       "payload": str(payload), "control": str(control)})
        self.paths[name], self.controls[name] = payload, control
        self.inventories[payload] = inventory
        return key

    def select(self, base_package_metadata=None):
        lock_path, mapping_path = self.root / "lock.json", self.root / "mapping.json"
        lock_path.write_text(json.dumps(self.content))
        mapping_path.write_text(json.dumps(self.mapping))
        return subject.select(lock_path, mapping_path, group="routing", architecture="arm64",
                              installed=self.installed, base_files=self.base,
                              retained_packages=self.retained, inspect_payload=self.inspect,
                              check_overlay=self.overlay, base_package_metadata=base_package_metadata)

    def test_base_and_explicitly_retained_packages_keep_their_existing_content(self):
        paths, receipt = self.select()
        self.assertEqual(paths, [self.paths["router"]])
        self.assertEqual(receipt["group"], "routing")
        self.assertEqual(receipt["skipped_base"][0]["base_version"], "3.5.7+fips")
        self.assertEqual(receipt["skipped_retained"][0]["source_sha256"], "d" * 64)
        self.inspect.assert_called_once_with(self.paths["router"])
        self.overlay.assert_called_once_with(self.inventories[self.paths["router"]], self.base)

    def update_control(self, name, text):
        """Update a reviewed control fixture and its locked size/hash together."""
        path = self.controls[name]
        write_tar(path, [("control", text.encode())])
        package = next(value for value in self.content["packages"].values() if value["name"] == name)
        package.update(control_sha256=digest(path.read_bytes()), control_size=path.stat().st_size)

    def test_final_base_version_must_satisfy_selected_package_dependencies(self):
        """Keep a compatible FIPS base, but reject an older base even when its name matches."""
        self.update_control("router", "Package: router\nVersion: 1.0\nArchitecture: arm64\n"
                            "Depends: libssl (>= 3.0)\n")
        paths, receipt = self.select()
        self.assertEqual(paths, [self.paths["router"]])
        self.assertEqual(receipt["dependency_check"]["status"], "satisfied")
        self.installed["libssl"] = dependencies.Package("libssl", "2.9", "arm64", origin="OCI base")
        self.overlay.reset_mock()
        with self.assertRaisesRegex(ValueError, "router.*libssl"):
            self.select()
        self.overlay.assert_not_called()

    def test_control_identity_cannot_disagree_with_the_lock(self):
        """Reject a reviewed control archive whose package identity disagrees with its lock entry."""
        for field, value in (("Package", "different"), ("Version", "2.0"), ("Architecture", "amd64")):
            with self.subTest(field=field):
                fields = {"Package": "router", "Version": "1.0", "Architecture": "arm64"}
                fields[field] = value
                self.update_control("router", "".join(key + ": " + val + "\n" for key, val in fields.items()))
                with self.assertRaisesRegex(ValueError, "control identity differs"):
                    self.select()
        self.inspect.assert_not_called()

    def test_retained_package_requires_complete_dependency_metadata(self):
        """Fail on hash-only retention because it cannot prove the supplied package version."""
        del self.retained["local-net"]["control"]
        with self.assertRaisesRegex(ValueError, "retained package lacks Debian control metadata"):
            self.select()

    def test_make_retained_versions_and_dependencies_participate_in_validation(self):
        """Validate retained package requirements and use its actual version to satisfy consumers."""
        self.update_control("router", "Package: router\nVersion: 1.0\nArchitecture: arm64\n"
                            "Pre-Depends: local-net (>= 2.0)\n")
        self.select()
        self.retained["local-net"]["control"]["Version"] = "1.0"
        with self.assertRaisesRegex(ValueError, "router.*local-net"):
            self.select()
        self.retained["local-net"]["control"].update(Version="2.0", Depends="missing-runtime")
        with self.assertRaisesRegex(ValueError, "local-net.*missing-runtime"):
            self.select()

    def test_base_control_dependencies_and_providers_survive_parsing(self):
        """Preserve folded dependencies and virtual providers from the base's dpkg status."""
        path = self.root / "base.tar"
        status = ("Package: libssl\nVersion: 3.5.7+fips\nArchitecture: arm64\n"
                  "Status: install ok installed\nProvides: tls-api (= 3.0)\n"
                  "Depends: libc6 (>= 2.30),\n libsupport\nMulti-Arch: same\n")
        write_tar(path, [("var/lib/dpkg/status", status.encode())])
        record = subject.base_packages([path], architecture="arm64")["libssl"]
        self.assertIn("libsupport", record.depends)
        self.assertEqual(record.provides, "tls-api (= 3.0)")
        self.assertEqual(record.multi_arch, "same")

    def test_child_layer_checks_inherited_apt_metadata_absent_from_dpkg_status(self):
        """Carry runtime additions into debug checks even though the original dpkg status stays unchanged."""
        self.update_control("router", "Package: router\nVersion: 1.0\nArchitecture: arm64\n"
                            "Depends: libssl (>= 3.0)\n")
        _, receipt = self.select()
        metadata = self.root / "runtime.selection.json"
        metadata.write_text(json.dumps(receipt))
        self.add_package("debug-tool", {"usr/bin/debug-tool": self.elf})
        self.update_control("debug-tool", "Package: debug-tool\nVersion: 1.0\nArchitecture: arm64\n"
                            "Depends: router (>= 1.0)\n")
        self.mapping["locked"] = [item for item in self.mapping["locked"] if item["key"] != "/trixie/router:arm64=1.0"]
        del self.content["dependency_sets"]["routing"]["sets"]["arm64"]["/trixie/router:arm64"]
        with self.assertRaisesRegex(ValueError, "debug-tool.*router"):
            self.select()
        paths, child = self.select(metadata)
        self.assertEqual(paths, [self.paths["debug-tool"]])
        self.assertEqual(child["dependency_check"]["packages"]["router"]["Depends"], "libssl (>= 3.0)")
        self.assertEqual(child["dependency_check"]["package_count"], 4)
        # Requirements inherited from runtime must also survive into validation.
        receipt["dependency_check"]["packages"]["router"]["Depends"] = "missing-runtime"
        metadata.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "router.*missing-runtime"):
            self.select(metadata)

    def test_inherited_inventory_must_match_the_current_dpkg_baseline(self):
        """Reject a receipt from a different base instead of silently overriding its package metadata."""
        _, receipt = self.select()
        metadata = self.root / "runtime.selection.json"
        metadata.write_text(json.dumps(receipt))
        self.installed["libssl"] = dependencies.Package("libssl", "3.6.0", "arm64", origin="OCI base")
        with self.assertRaisesRegex(ValueError, "does not match the inherited dpkg inventory"):
            self.select(metadata)

    def test_inherited_inventory_cannot_omit_or_misname_installed_packages(self):
        """Fail on incomplete or inconsistent dependency receipts before checking consumer compatibility."""
        _, receipt = self.select()
        metadata = self.root / "runtime.selection.json"
        changed = copy.deepcopy(receipt)
        del changed["dependency_check"]["packages"]["libssl"]
        metadata.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "omits installed packages"):
            self.select(metadata)
        changed = copy.deepcopy(receipt)
        changed["dependency_check"]["packages"]["router"]["Package"] = "wrong-name"
        metadata.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "name differs from control record"):
            self.select(metadata)
        changed = copy.deepcopy(receipt)
        changed["dependency_check"]["packages"]["libssl"]["Version"] = "99.0"
        metadata.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "changes an installed package control record"):
            self.select(metadata)

    def test_malformed_inherited_inventory_reports_a_validation_error(self):
        """Reject wrong-shaped receipt JSON explicitly instead of losing dependency information."""
        _, receipt = self.select()
        malformed = copy.deepcopy(receipt)
        malformed["dependency_check"]["packages"]["router"] = []
        metadata = self.root / "runtime.selection.json"
        for document in ([], {}, {"dependency_check": []}, malformed):
            with self.subTest(document=document):
                metadata.write_text(json.dumps(document))
                with self.assertRaisesRegex(ValueError, "base package metadata"):
                    self.select(metadata)

    def test_retained_metadata_cannot_replace_a_known_base_package(self):
        """Reject contradictory retention metadata instead of inventing a compatible base version."""
        self.retained["libssl"] = {"source_sha256": "0" * 64, "control": {
            "Package": "libssl", "Version": "99.0", "Architecture": "arm64"}}
        with self.assertRaisesRegex(ValueError, "conflicts with inherited package: libssl"):
            self.select()

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

    def test_missing_candidate_cannot_bypass_the_reviewed_closure(self):
        self.mapping["locked"].pop()
        with self.assertRaisesRegex(ValueError, "do not match the reviewed dependency set"):
            self.select()
        self.inspect.assert_not_called()

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
        self.installed["router"] = dependencies.Package("router", "1.0", "arm64")
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
        self.assertEqual({name: package.version for name, package in subject.base_packages([path], architecture="arm64").items()},
                         {"router": "1.0", "docs": "2.0"})
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
                self.assertEqual(subject.base_packages([base, update], architecture="arm64")["router"].version, "2")


if __name__ == "__main__":
    unittest.main()
