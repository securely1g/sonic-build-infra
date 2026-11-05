"""Real fixed-uv integration for the public runtime-lock update/check functions.

Run with --uv PATH. The resolver reads valid local wheel fixtures and their
standard metadata; no resolver result is mocked and every hash comes from bytes.
"""

import argparse
import base64
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from runtime_lock import RuntimeLockError, check, locked_packages, update
from runtime_lock_test import plan, source


def wheel(directory, name, version, requires=()):
    canonical = name.replace("-", "_")
    filename = f"{canonical}-{version}-py3-none-any.whl"
    dist = f"{canonical}-{version}.dist-info"
    files = {
        canonical + ".py": f"VERSION = '{version}'\n".encode(),
        dist + "/METADATA": (f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" +
                             "".join("Requires-Dist: " + dep + "\n" for dep in requires)).encode(),
        dist + "/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for path, contents in sorted(files.items()):
        writer.writerow([path, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b"=").decode(), len(contents)])
    writer.writerow([dist + "/RECORD", "", ""])
    files[dist + "/RECORD"] = record.getvalue().encode()
    output = directory / filename
    with zipfile.ZipFile(output, "w") as archive:
        for path, contents in files.items():
            archive.writestr(path, contents)
    return output


class RealUvUpdateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.links = self.root / "wheels"
        self.links.mkdir()
        self.req, self.lock, self.inputs = [self.root / name for name in ("requirements.in", "requirements_lock.txt", "lock-inputs.json")]
        self.cache = self.root / "uv-cache"
        self.wheels = [wheel(self.links, "fixture-external", "1.0", ["fixture-transitive>=1"]),
                       wheel(self.links, "fixture-external", "2.0", ["fixture-transitive>=1"]),
                       wheel(self.links, "fixture-transitive", "1.0"),
                       wheel(self.links, "fixture-added", "1.0")]

    def package_plan(self, requires):
        return plan([source("image-service", requires=requires)], roots=["image-service"],
                    find_links=[str(self.links)], python_version="3.11")

    def run_update(self, value):
        return update(value, self.req, self.lock, self.inputs, UV, self.cache)

    def test_real_resolution_hashes_and_transitive_versions(self):
        value = self.package_plan(["fixture-external<2"])
        self.run_update(value)
        packages = locked_packages(self.lock.read_text())
        self.assertEqual(set(packages), {"fixture-external", "fixture-transitive"})
        self.assertEqual(packages["fixture-external"]["version"], "1.0")
        for name, package in packages.items():
            artifact = next(path for path in self.wheels if path.name.startswith(name.replace("-", "_") + "-" + package["version"] + "-"))
            self.assertIn(hashlib.sha256(artifact.read_bytes()).hexdigest(), package["hashes"])
        self.assertNotIn("image-service", packages)
        check(value, self.req, self.lock, self.inputs)

    def test_existing_versions_retained_while_inputs_and_roots_change(self):
        self.run_update(self.package_plan(["fixture-external<2"]))
        original = self.lock.read_bytes()
        value = self.package_plan(["fixture-external<3", "fixture-added"])
        self.run_update(value)
        packages = locked_packages(self.lock.read_text())
        self.assertEqual(packages["fixture-external"]["version"], "1.0")
        self.assertIn("fixture-added", packages)
        self.run_update(self.package_plan(["fixture-external<3"]))
        self.assertEqual(self.lock.read_bytes(), original)

    def test_resolution_failure_preserves_all_valid_delivery_files(self):
        self.run_update(self.package_plan(["fixture-external<2"]))
        before = [path.read_bytes() for path in (self.req, self.lock, self.inputs)]
        with self.assertRaisesRegex(RuntimeLockError, "uv dependency resolution failed"):
            self.run_update(self.package_plan(["fixture-external>=99"]))
        self.assertEqual(before, [path.read_bytes() for path in (self.req, self.lock, self.inputs)])

    def test_new_source_constraint_stales_lock_even_when_external_set_is_equal(self):
        original = plan([source("image-service", requires=["common>=1", "fixture-external<2"]), source("common")],
                        roots=["image-service"], find_links=[str(self.links)], python_version="3.11")
        self.run_update(original)
        lock_bytes = self.lock.read_bytes()
        changed = copy.deepcopy(original)
        changed["sources"][0]["requires_dist"][0] = "common>=1.1"
        with self.assertRaisesRegex(RuntimeLockError, "stale"):
            check(changed, self.req, self.lock, self.inputs)
        self.run_update(changed)
        self.assertEqual(self.lock.read_bytes(), lock_bytes)
        check(changed, self.req, self.lock, self.inputs)

    def test_wrong_tool_version_preserves_existing_outputs(self):
        value = self.package_plan(["fixture-external<2"])
        self.run_update(value)
        before = [path.read_bytes() for path in (self.req, self.lock, self.inputs)]
        value["uv_version"] = "0.0.0"
        with self.assertRaisesRegex(RuntimeLockError, "expected uv"):
            self.run_update(value)
        self.assertEqual(before, [path.read_bytes() for path in (self.req, self.lock, self.inputs)])

    def test_normal_check_is_read_only_and_detects_lock_tampering(self):
        value = self.package_plan(["fixture-external<2"])
        self.run_update(value)
        before = [path.read_bytes() for path in (self.req, self.lock, self.inputs)]
        check(value, self.req, self.lock, self.inputs)
        self.assertEqual(before, [path.read_bytes() for path in (self.req, self.lock, self.inputs)])
        self.lock.write_text(self.lock.read_text() + "# changed\n")
        with self.assertRaisesRegex(RuntimeLockError, "lock content changed"):
            check(value, self.req, self.lock, self.inputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv", type=Path, required=True)
    args, other = parser.parse_known_args()
    UV = args.uv.resolve()
    unittest.main(argv=[sys.argv[0]] + other)
