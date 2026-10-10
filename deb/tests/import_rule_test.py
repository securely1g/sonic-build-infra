"""Exercise declared importer outputs from existing packages, without installation."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import unittest

INPUTS = list(map(Path, sys.argv[1:]))
del sys.argv[1:]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def member(archive, name):
    return next(item for item in reversed(archive.getmembers())
                if item.name.removeprefix('./').rstrip('/') == name)


class ImportRuleTest(unittest.TestCase):
    def test_public_outputs_and_maintainer_scripts_are_data(self):
        payload, manifest, controls = INPUTS[:3]
        record = json.loads(manifest.read_text())
        self.assertEqual(record['payload']['sha256'], digest(payload))
        self.assertEqual(record['packages'][0]['control_output_sha256'], digest(controls))
        self.assertEqual(record['image'], 'example-image')
        self.assertEqual(record['packages'][0]['package'], 'example-gz')
        with tarfile.open(payload) as archive:
            self.assertEqual(archive.extractfile(member(archive, 'usr/share/example/value')).read(), b'gz')
        with tarfile.open(controls) as archive:
            # Executing this checked-in script would fail the import with exit99.
            script = member(archive, 'postinst')
            self.assertEqual(archive.extractfile(script).read(), b'#!/bin/sh\nexit 99\n')
            self.assertEqual(script.mode, 0o755)

    def test_payload_fidelity(self):
        with tarfile.open(INPUTS[0]) as archive:
            expected = {
                'usr/share/example': (tarfile.DIRTYPE, 0o750),
                'usr/share/example/value': (tarfile.REGTYPE, 0o640),
                'usr/share/example/symlink': (tarfile.SYMTYPE, 0o777),
                'usr/share/example/hardlink': (tarfile.LNKTYPE, 0o640),
            }
            for name, (kind, mode) in expected.items():
                with self.subTest(path=name):
                    entry = member(archive, name)
                    self.assertEqual(entry.type, kind)
                    self.assertEqual(entry.mode, mode)
                    self.assertEqual((entry.uid, entry.gid), (123, 456))
                    self.assertEqual((entry.uname, entry.gname), ('sonic-test', 'sonic-test-group'))
                    self.assertEqual(entry.mtime, 1700000000)
                    self.assertEqual(entry.pax_headers['SCHILY.xattr.user.example'], 'preserved')
            self.assertEqual(member(archive, 'usr/share/example/symlink').linkname, 'value')
            self.assertEqual(member(archive, 'usr/share/example/hardlink').linkname.removeprefix('./'),
                             'usr/share/example/value')
            self.assertEqual(archive.extractfile(member(archive, 'usr/share/example/hardlink')).read(), b'gz')

    def test_independent_actions_are_byte_reproducible(self):
        for original, repeated in zip(INPUTS[:3], INPUTS[3:6]):
            with self.subTest(output=original.name):
                self.assertEqual(digest(original), digest(repeated))

    def test_declared_package_order_controls_the_overlay(self):
        payload, manifest = INPUTS[6:8]
        record = json.loads(manifest.read_text())
        self.assertEqual([item['package'] for item in record['packages']], ['example-gz', 'example-xz'])
        self.assertEqual(record['payload']['sha256'], digest(payload))
        self.assertNotEqual(digest(payload), digest(INPUTS[0]))
        with tarfile.open(payload) as archive:
            self.assertEqual(archive.extractfile(member(archive, 'usr/share/example/value')).read(), b'xz')
            self.assertEqual(archive.extractfile(member(archive, 'usr/share/example/hardlink')).read(), b'xz')

    def test_debug_manifest_binds_runtime_bytes(self):
        runtime, debug = (json.loads(INPUTS[index].read_text()) for index in (1, 8))
        self.assertEqual(debug['runtime_manifest_sha256'], digest(INPUTS[1]))
        self.assertEqual(debug['packages'][0]['source_sha256'], runtime['packages'][0]['source_sha256'])
        self.assertEqual(debug['image'], 'example-image-debug')
        self.assertEqual([item['package'] for item in debug['packages']], ['example-gz', 'example-xz'])


if __name__ == '__main__':
    unittest.main()
