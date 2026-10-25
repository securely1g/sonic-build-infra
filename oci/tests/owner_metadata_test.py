"""Owner metadata follows selected modules instead of copied image versions."""

import json
from pathlib import Path
import tempfile
import unittest

from sonic_oci.owner_metadata import resolve


class OwnerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.module = self.root / "MODULE.bazel"
        self.module.write_text('module(name="example", version="1.2.3.sonic.1")\n')
        self.metadata = self.root / "packages.json"
        self.document = {"schema": 1, "module": "example", "packages": {"libexample": {
            "runtime_target": "//dist:runtime", "control_fields": {"Package": "libexample", "Version": "1.2.3-1"}}}}
        self.spec = {"schema": 1, "packages": {"libexample": {"required_paths": ["usr/lib/libexample.so"]}}}

    def resolve(self, *, module="example", target="@@example+//dist:runtime"):
        self.metadata.write_text(json.dumps(self.document))
        return resolve(self.spec, {"@@example+//bazel:runtime_packages.json": self.metadata},
                       {module: self.module}, {"libexample": target}, "arm64")

    def test_uses_selected_module_and_declared_target(self):
        spec, modules = self.resolve()
        entry = spec["packages"]["libexample"]
        self.assertEqual(entry["source_revision"], "1.2.3.sonic.1")
        self.assertEqual(entry["control_fields"]["Architecture"], "arm64")
        self.assertEqual(entry["target"], "@@example+//dist:runtime")
        self.assertEqual(len(modules["example"]["module_sha256"]), 64)

    def test_image_can_omit_checks_supplied_by_the_producer(self):
        self.document["packages"]["libexample"]["required_paths"] = ["usr/lib/{multiarch}/libswsscommon.so.0"]
        self.spec["packages"] = {}
        resolved, _ = self.resolve()
        self.assertEqual(resolved["packages"]["libexample"]["required_paths"],
                         ["usr/lib/{multiarch}/libswsscommon.so.0"])

    def test_image_additions_cannot_replace_the_producer_checks(self):
        self.document["packages"]["libexample"]["required_paths"] = ["usr/lib/{multiarch}/libswsscommon.so.0"]
        self.spec["packages"]["libexample"]["required_paths"] = ["run/redis/sonic-db/database_config.json"]
        resolved, _ = self.resolve()
        self.assertEqual(resolved["packages"]["libexample"]["required_paths"], [
            "usr/lib/{multiarch}/libswsscommon.so.0", "run/redis/sonic-db/database_config.json"])

    def test_legacy_typed_paths_preserve_the_source_contract_classifications(self):
        typed = {"usr/lib/libswsscommon.so.0": "symlink", "usr/lib/libswsscommon.so.0.0.0": "elf"}
        self.spec["packages"]["libexample"]["required_paths"] = typed
        self.document["packages"]["libexample"]["required_paths"] = ["var/run/redis/sonic-db/database_config.json"]
        resolved, _ = self.resolve()
        record = resolved["packages"]["libexample"]
        self.assertEqual(record["required_paths"], typed)
        self.assertEqual(record["producer_required_paths"], ["var/run/redis/sonic-db/database_config.json"])
        del self.document["packages"]["libexample"]["required_paths"]
        legacy, _ = self.resolve()
        self.assertEqual(legacy["packages"]["libexample"]["required_paths"], typed)
        self.assertNotIn("producer_required_paths", legacy["packages"]["libexample"])

    def test_unmapped_image_packages_and_missing_combined_coverage_are_rejected(self):
        self.spec["packages"]["typo"] = {"required_paths": ["usr/bin/program"]}
        with self.assertRaisesRegex(ValueError, "undeclared source TARs"):
            self.resolve()
        self.spec["packages"] = {}
        with self.assertRaisesRegex(ValueError, "lacks producer or image required paths"):
            self.resolve()

    def test_malformed_producer_paths_are_rejected(self):
        for paths in ("usr/bin/program", ["../escape"], ["usr/{unknown}/program"]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.document["packages"]["libexample"]["required_paths"] = paths
                self.resolve()

    def test_sonic_commit_version_exposes_actual_commit(self):
        self.module.write_text('module(name="example", version="0.0.1-' + "a" * 40 + '")\n')
        self.assertEqual(self.resolve()[0]["packages"]["libexample"]["source_revision"], "a" * 40)

    def test_root_module_uses_workspace_identity(self):
        self.document["module"] = "sonic-buildimage"
        self.module.write_text('module(name="sonic-buildimage")\n')
        self.assertEqual(self.resolve(module="sonic-buildimage")[0]["packages"]["libexample"]["source_revision"], "workspace")

    def test_runtime_target_must_belong_to_declared_owner(self):
        for target in ("@@other+//dist:runtime", "@@example+//dist:wrong"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "owner runtime target"):
                self.resolve(target=target)

    def test_image_cannot_override_owner_version(self):
        self.spec["packages"]["libexample"]["control_fields"] = {"Version": "old"}
        with self.assertRaisesRegex(ValueError, "duplicates owner"):
            self.resolve()

    def test_selected_module_must_match_owner_name(self):
        self.module.write_text('module(name="unexpected", version="1.0")\n')
        with self.assertRaisesRegex(ValueError, "module declaration differs"):
            self.resolve()

    def test_metadata_cannot_freeze_installed_architecture(self):
        self.document["packages"]["libexample"]["control_fields"]["Architecture"] = "amd64"
        with self.assertRaisesRegex(ValueError, "freeze installed"):
            self.resolve()

    def test_generated_metadata_must_match_target_architecture(self):
        """Do not reuse resolved AMD64 dependency metadata for ARM64 payloads."""
        self.document["architecture"] = "amd64"
        with self.assertRaisesRegex(ValueError, "selected target architecture"):
            self.resolve()

    def test_generated_payload_hash_is_preserved_and_validated(self):
        """Carry the generated hash to the final image's independent TAR check."""
        package = self.document["packages"]["libexample"]
        package["payload_sha256"] = "a" * 64
        self.assertEqual(self.resolve()[0]["packages"]["libexample"]["payload_sha256"], "a" * 64)
        package["payload_sha256"] = "unverified"
        with self.assertRaisesRegex(ValueError, "source payload identity"):
            self.resolve()


if __name__ == "__main__":
    unittest.main()
