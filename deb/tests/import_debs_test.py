"""Check existing-package import fidelity, ordering, metadata and rejection paths.

These tests consume checked-in DEBs. Regeneration is an explicit host operation
in make_fixtures.py; neither this suite nor its Bazel graph builds a package.
"""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

import import_debs

FIXTURES = Path(__file__).parent / 'fixtures'


class ImportDebsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def import_packages(self, kinds=('gz',), architecture='amd64', metadata=None, runtime=None):
        mapping = {'architecture': architecture, 'metadata': metadata or {}, 'packages': [
            {'path': str(FIXTURES / f'example-{kind}_1.0-1_all.deb'),
             'label': f'//packages:example-{kind}', 'control': str(self.root / f'{index}.control.tar')}
            for index, kind in enumerate(kinds)]}
        return import_debs.import_packages(mapping, self.root / 'payload.tar', self.root / 'manifest.json', runtime)

    def test_original_headers_ownership_links_and_package_order(self):
        """Import all supported codecs without rewriting original TAR headers or owners."""
        result = self.import_packages(('gz', 'xz', 'bz2', 'raw'))
        self.assertEqual([p['package'] for p in result['packages']], ['example-' + k for k in ('gz', 'xz', 'bz2', 'raw')])
        self.assertEqual(result['payload']['members'], 16)
        with tarfile.open(self.root / 'payload.tar') as archive:
            members = archive.getmembers()
            values = [archive.extractfile(m).read() for m in members if m.isfile()]
            self.assertEqual(values, [b'gz', b'xz', b'bz2', b'raw'])
            for member in members:
                self.assertEqual((member.uid, member.gid, member.uname, member.gname),
                                 (123, 456, 'sonic-test', 'sonic-test-group'))
                self.assertEqual(member.mtime, 1700000000)
                self.assertEqual(member.pax_headers['SCHILY.xattr.user.example'], 'preserved')
            self.assertEqual(members[2].linkname, 'value')
            self.assertEqual(members[3].linkname, './usr/share/example/value')
            self.assertEqual(members[1].mode, 0o640)
        original = self.root / 'original'
        original.mkdir()
        _, data = import_debs.unpack_ar(FIXTURES / 'example-gz_1.0-1_all.deb', original)
        import_debs.decompress(data, original / 'raw.tar')
        _, extent = import_debs.payload_extent(original / 'raw.tar')
        self.assertEqual((self.root / 'payload.tar').read_bytes()[:extent], (original / 'raw.tar').read_bytes()[:extent])

    def test_records_actual_controls_and_source_provenance(self):
        """APT gets complete dependency fields; hashes describe the exact consumed DEB."""
        result = self.import_packages(metadata={'image': 'example-image', 'features': {'fips': 'y'}})
        record = result['packages'][0]
        self.assertEqual(record['source_label'], '//packages:example-gz')
        self.assertEqual(record['source_sha256'], json.loads((FIXTURES / 'sha256.json').read_text())[record['source_deb']])
        self.assertEqual(record['control_fields']['Depends'], 'libc6 (>= 2.38), alternate-a | alternate-b')
        self.assertEqual(record['control_fields']['Pre-Depends'], 'base-files')
        self.assertEqual(record['control_fields']['Description'], 'example import package\n preserves multiline fields')
        self.assertEqual(record['control_files']['postinst'], hashlib.sha256(b'#!/bin/sh\nexit 99\n').hexdigest())
        self.assertEqual(result['features'], {'fips': 'y'})
        self.assertEqual(import_debs.sha(self.root / 'payload.tar'), result['payload']['sha256'])
        self.assertEqual(import_debs.sha(self.root / '0.control.tar'), record['control_sha256'])

    def test_parent_manifest_binds_runtime_package_bytes(self):
        """Debug imports may repeat runtime bytes but cannot replace that package identity."""
        runtime = self.import_packages()
        parent = self.root / 'parent.json'
        parent.write_text(json.dumps(runtime))
        result = self.import_packages(runtime=parent)
        self.assertEqual(result['runtime_manifest_sha256'], import_debs.sha(parent))
        runtime['packages'][0]['source_sha256'] = '0' * 64
        parent.write_text(json.dumps(runtime))
        with self.assertRaisesRegex(ValueError, 'replaces a runtime package'):
            self.import_packages(runtime=parent)

    def test_foreign_architecture_rejected(self):
        """A native package cannot enter an image with a different Debian architecture."""
        mapping = {'architecture': 'amd64', 'packages': [{
            'path': str(FIXTURES / 'example-foreign_1.0-1_arm64.deb'),
            'label': '//packages:foreign', 'control': str(self.root / 'control.tar')}]}
        with self.assertRaisesRegex(ValueError, 'foreign Debian architecture'):
            import_debs.import_packages(mapping, self.root / 'payload.tar', self.root / 'manifest.json')

    def test_duplicate_package_rejected(self):
        """An ambiguous duplicate package input cannot silently win by ordering."""
        with self.assertRaisesRegex(ValueError, 'duplicate Debian package'):
            self.import_packages(('gz', 'gz'))

    def test_required_package_rejected(self):
        """A caller can require a package without overriding its extracted identity."""
        with self.assertRaisesRegex(ValueError, 'missing required'):
            self.import_packages(metadata={'required_packages': ['absent']})

    def test_caller_cannot_forge_verified_metadata(self):
        """Consumer context cannot replace the importer-owned package/hash fields."""
        for field in ('schema', 'architecture', 'packages', 'payload', 'runtime_manifest_sha256'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'overrides verified'):
                self.import_packages(metadata={field: 'forged'})

    def test_control_duplicate_case_and_orphan_continuation(self):
        """Reject controls with multiple interpretations before dependency selection."""
        for text in (' orphan\n', 'Package: a\npackage: b\n', 'not a field\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_debs.control_fields(text)

    def test_unsafe_archive_paths(self):
        """Reject host-absolute and parent-traversing payload and hardlink paths."""
        for name in ('/etc/passwd', '../etc/passwd', 'usr/../../etc/passwd'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unsafe archive path'):
                import_debs.safe_name(name)

    def test_rejects_payload_hidden_after_end_markers(self):
        """A second TAR hidden after end markers must not evade the recorded inventory."""
        path = self.root / 'hidden.tar'
        with tarfile.open(path, 'w') as archive:
            member = tarfile.TarInfo('ok')
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
        with path.open('ab') as stream:
            stream.write(b'hidden content')
        with self.assertRaisesRegex(ValueError, 'unexpected data after'):
            import_debs.payload_extent(path)

    def test_rejects_reserved_whiteout(self):
        """Imported package paths cannot delete unrelated base image content as OCI whiteouts."""
        path = self.root / 'whiteout.tar'
        with tarfile.open(path, 'w') as archive:
            archive.addfile(tarfile.TarInfo('usr/.wh.bin'))
        with self.assertRaisesRegex(ValueError, 'reserved OCI whiteout'):
            import_debs.payload_extent(path)

    def test_unsupported_compression_fails_explicitly(self):
        """New compression formats cannot trigger a host-tool fallback or reinterpretation."""
        with self.assertRaisesRegex(ValueError, 'unsupported Debian compression'):
            import_debs.decompress(self.root / 'data.tar.zst', self.root / 'output.tar')


if __name__ == '__main__':
    unittest.main()
