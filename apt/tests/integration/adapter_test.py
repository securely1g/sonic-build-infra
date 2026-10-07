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


if __name__ == "__main__":
    PAYLOAD, ADDED, RETAINED, ADDED_RECEIPT, RETAINED_RECEIPT = sys.argv[1:]
    unittest.main(argv=[sys.argv[0]])
