"""Inspect the public rule's real status and receipt outputs."""

import hashlib
import json
from pathlib import Path
import sys
import unittest

status, receipt, source = map(Path, sys.argv[1:])

class PublicRuleTest(unittest.TestCase):
    def test_owner_and_payload_are_bound_to_installed_inventory(self):
        content = status.read_text()
        self.assertIn("Package: base-files\n", content)
        self.assertIn("Package: example\n", content)
        self.assertIn("Version: 2.0\n", content)
        self.assertIn("Description: Source-owned package\n", content)
        document = json.loads(receipt.read_text())
        record, = document["source_packages"]
        self.assertEqual(record["payload_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertTrue(record["target"].endswith("//oci/tests:metadata_source.tar"))
        self.assertEqual(record["source_revision"], "1.0.0.sonic.1")
        self.assertEqual(document["dependency_check"]["status"], "satisfied")
        self.assertEqual(document["source_modules"]["example"]["version"], "1.0.0.sonic.1")

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
