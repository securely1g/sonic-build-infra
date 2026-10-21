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


if __name__ == "__main__":
    unittest.main()
