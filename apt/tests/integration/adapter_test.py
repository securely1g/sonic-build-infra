"""Check real package-only inputs and normal Distroless assembly after selection."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import unittest


def inventory(path):
    with tarfile.open(path) as archive:
        return [(str(PurePosixPath(m.name)), m.type, m.mode, m.uid, m.gid, m.linkname,
                 hashlib.sha256(archive.extractfile(m).read()).hexdigest() if m.isfile() else None)
                for m in archive]


class AdapterTest(unittest.TestCase):
    def test_existing_public_package_data_is_assembled_unchanged(self):
        self.assertEqual(inventory(PAYLOAD), inventory(ADDED))
        receipt = json.loads(Path(ADDED_RECEIPT).read_bytes())
        self.assertEqual([p["package"] for p in receipt["selected"]], ["aspell-en"])
        self.assertEqual(receipt["skipped_base"], [])

    def test_base_package_is_automatically_skipped_even_with_a_different_version(self):
        self.assertEqual(inventory(RETAINED), [])
        receipt = json.loads(Path(RETAINED_RECEIPT).read_bytes())
        self.assertEqual(receipt["selected"], [])
        self.assertEqual(receipt["skipped_base"][0]["base_version"], "2021.1")

    def test_policy_is_read_without_a_retained_manifest(self):
        """A declared policy changes actual assembly without needing a Make manifest."""
        self.assertEqual(inventory(POLICY_ONLY), [])
        receipt = json.loads(Path(POLICY_ONLY_RECEIPT).read_bytes())
        self.assertEqual(receipt["policy"], {"retain_dictionary": True, "requires_retained_manifest": False})
        self.assertFalse(receipt["retained_manifest_supplied"])
        self.assertEqual(receipt["selected"], [])
        self.assertEqual(receipt["skipped_base"][0]["base_version"], "2021.1")

    def test_policy_and_manifest_are_independent_declared_inputs(self):
        """The selector reads both files and applies policy before flattening unchanged TARs."""
        self.assertEqual(inventory(POLICY_AND_MANIFEST), inventory(PAYLOAD))
        receipt = json.loads(Path(POLICY_AND_MANIFEST_RECEIPT).read_bytes())
        self.assertEqual(receipt["policy"], {"retain_dictionary": False, "requires_retained_manifest": True})
        self.assertTrue(receipt["retained_manifest_supplied"])
        self.assertEqual([p["package"] for p in receipt["selected"]], ["aspell-en"])
        self.assertEqual(receipt["skipped_base"], [])


if __name__ == "__main__":
    (PAYLOAD, ADDED, RETAINED, ADDED_RECEIPT, RETAINED_RECEIPT,
     POLICY_ONLY, POLICY_ONLY_RECEIPT, POLICY_AND_MANIFEST, POLICY_AND_MANIFEST_RECEIPT) = sys.argv[1:]
    unittest.main(argv=[sys.argv[0]])
