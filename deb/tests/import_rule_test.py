"""Exercise the declared Bazel importer and its public default/manifest/control outputs."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import unittest

INPUTS = sys.argv[1:]
del sys.argv[1:]


class ImportRuleTest(unittest.TestCase):
    def test_public_outputs(self):
        payload, manifest, controls = map(Path, INPUTS[:3])
        record = json.loads(manifest.read_text())
        self.assertEqual(record['payload']['sha256'], hashlib.sha256(payload.read_bytes()).hexdigest())
        self.assertEqual(record['packages'][0]['control_sha256'], hashlib.sha256(controls.read_bytes()).hexdigest())
        self.assertEqual(record['image'], 'example-image')
        self.assertEqual(record['packages'][0]['package'], 'example-gz')
        with tarfile.open(payload) as archive:
            self.assertEqual(archive.extractfile('./usr/share/example/value').read(), b'gz')
        with tarfile.open(controls) as archive:
            self.assertEqual(archive.extractfile('./postinst').read(), b'#!/bin/sh\nexit 99\n')


if __name__ == '__main__':
    unittest.main()
