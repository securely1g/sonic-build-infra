"""dpkg payload contract: parent-first archives preserve declared metadata and inodes.

Regression source: real dpkg unpack of sonic_deploy_tar's debug archive failed
because .build-id preceded usr/lib/debug. Missing parents are a separate valid
input category; the assembler supplies root-owned 0755 directories. These tests
inspect archive semantics, not the directory insertion algorithm.
"""
import gzip
import os
import sys
import hashlib
import io
import struct
from pathlib import Path
import tarfile
import tempfile
import unittest

from types import SimpleNamespace

from deb.assemble import append_gzip_tar, assemble, order_payload, summarize, summarize_mtree


class PayloadTest(unittest.TestCase):
    def test_late_explicit_parents_preserve_metadata_and_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.tar'
            destination = Path(temporary) / 'data.tar'
            content = b'independent debug bytes\0' * 60
            with tarfile.open(source, 'w', format=tarfile.PAX_FORMAT) as archive:
                # Reproduces a child directory preceding its explicit parents.
                child = tarfile.TarInfo('usr/lib/debug/.build-id')
                child.type = tarfile.DIRTYPE
                archive.addfile(child)
                file = tarfile.TarInfo('usr/lib/debug/.build-id/symbols')
                file.size = len(content)
                file.uid, file.gid, file.mode, file.mtime = 123, 456, 0o640, 101
                archive.addfile(file, io.BytesIO(content))
                hard = tarfile.TarInfo('usr/lib/debug/alias')
                hard.type, hard.linkname = tarfile.LNKTYPE, file.name
                archive.addfile(hard)
                symlink = tarfile.TarInfo('usr/lib/debug/current')
                symlink.type, symlink.linkname = tarfile.SYMTYPE, '.build-id/symbols'
                archive.addfile(symlink)
                for name in ['usr/lib/debug', '.', 'usr/lib', 'usr']:
                    directory = tarfile.TarInfo(name)
                    directory.type = tarfile.DIRTYPE
                    directory.uid, directory.gid = 1234, 4321
                    directory.mode, directory.mtime = 0o2750, 987654
                    directory.pax_headers = {'SCHILY.xattr.user.contract': 'explicit-value'}
                    archive.addfile(directory)
            order_payload(source, destination)
            with tarfile.open(destination) as archive:
                entries = archive.getmembers()
                names = [entry.name for entry in entries]
                for entry in entries:
                    parent = str(Path(entry.name).parent)
                    if entry.name != '.':
                        self.assertLess(names.index(parent), names.index(entry.name))
                for name in ['usr/lib/debug', '.', 'usr/lib', 'usr']:
                    entry = archive.getmember(name)
                    self.assertEqual((entry.uid, entry.gid, entry.mode, entry.mtime),
                                     (1234, 4321, 0o2750, 987654))
                    self.assertEqual(entry.pax_headers['SCHILY.xattr.user.contract'], 'explicit-value')
                entry = archive.getmember(file.name)
                self.assertEqual((entry.uid, entry.gid, entry.mode, entry.mtime), (123, 456, 0o640, 101))
                self.assertEqual(archive.extractfile(file.name).read(), content)
                self.assertEqual(archive.extractfile(hard.name).read(), content)
                self.assertTrue(archive.getmember(hard.name).islnk())
                self.assertEqual(archive.getmember(symlink.name).linkname, '.build-id/symbols')
            # Five directory inodes, two KiB of data counted once, one symlink.
            _, installed_size, sums = summarize(destination, 'all')
            self.assertEqual(installed_size, 8)
            digest = hashlib.md5(content).hexdigest()
            self.assertEqual(sums, digest + '  usr/lib/debug/.build-id/symbols\n' +
                             digest + '  usr/lib/debug/alias\n')

    def test_kernel_compatibility_elf_payloads_preserve_checksums(self):
        # Kernel debug packages contain native and compat VDSOs. The package
        # Architecture remains native; ELF e_machine is not a package invariant.
        for architecture, machines in [('amd64', (62, 3)), ('arm64', (183, 40))]:
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / 'data.tar'
                payloads = {}
                with tarfile.open(source, 'w') as archive:
                    for index, machine in enumerate(machines):
                        content = bytearray(64)
                        content[:7] = b'\x7fELF' + bytes((2 if index == 0 else 1, 1, 1))
                        struct.pack_into('<H', content, 18, machine)
                        name = 'usr/lib/debug/vdso-{}.so'.format(machine)
                        payloads[name] = bytes(content)
                        member = tarfile.TarInfo(name)
                        member.size = len(content)
                        archive.addfile(member, io.BytesIO(content))
                _, installed_size, sums = summarize(source, architecture)
                self.assertEqual(installed_size, 2)
                self.assertEqual(sums, ''.join(hashlib.md5(content).hexdigest() + '  ' + name + '\n'
                                             for name, content in sorted(payloads.items())))

    def test_missing_parents_get_default_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.tar'
            destination = Path(temporary) / 'data.tar'
            with tarfile.open(source, 'w') as archive:
                member = tarfile.TarInfo('opt/custom/payload')
                member.size = 1
                archive.addfile(member, io.BytesIO(b'x'))
            order_payload(source, destination)
            with tarfile.open(destination) as archive:
                self.assertEqual(archive.getnames(), ['.', 'opt', 'opt/custom', 'opt/custom/payload'])
                for name in ['.', 'opt', 'opt/custom']:
                    entry = archive.getmember(name)
                    self.assertTrue(entry.isdir())
                    self.assertEqual((entry.uid, entry.gid, entry.mode, entry.mtime), (0, 0, 0o755, 0))
                self.assertEqual(archive.extractfile('opt/custom/payload').read(), b'x')


class MtreePayloadTest(unittest.TestCase):
    # The mtree input must preserve the same file bytes and installation
    # metadata as a Debian payload, including escaped paths and real links.
    def test_file_sources_and_links_determine_checksums_and_installed_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source file'
            content = b'independent payload' * 100
            source.write_bytes(content)
            spec = root / 'data.mtree'
            escaped = str(source).replace(' ', r'\040')
            spec.write_text('#mtree\n./. type=dir\n./usr type=dir\n'
                            './usr/debug\\040file type=file mode=0640 contents=' + escaped + '\n'
                            './usr/current type=link link=debug\\040file\n')
            entries, installed_size, sums = summarize_mtree(spec, 'amd64')
            self.assertEqual(entries['usr/debug file']['mode'], '0640')
            self.assertEqual(installed_size, 5)  # two dirs, two KiB, one symlink
            self.assertEqual(sums, hashlib.md5(content).hexdigest() + '  usr/debug file\n')
            self.assertEqual(source.read_bytes(), content)

    def test_missing_parent_and_unsafe_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            spec = Path(temporary) / 'data.mtree'
            for text, error in [('./usr/lib type=dir', 'missing mtree parent'),
                                ('../escape type=dir', 'unsafe data path'),
                                ('./. type=dir\n./. type=dir', 'duplicate mtree path')]:
                with self.subTest(text=text):
                    spec.write_text('#mtree\n' + text + '\n')
                    with self.assertRaisesRegex(ValueError, error):
                        summarize_mtree(spec, 'amd64')


class StreamingMemberTest(unittest.TestCase):
    # deb(5) requires ar members with decimal lengths and even-byte alignment.
    # A standard gzip decoder must recover the exact bytes of each member.
    def test_compressed_members_preserve_bytes_and_ar_alignment(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'example.deb'
            archive.write_bytes(b'!<arch>\n')
            payloads = [('control.tar.gz', b''), ('data.tar.gz', b'a')]
            for name, content in payloads:
                command = [sys.executable, '-c', 'import sys;sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))', content.hex()]
                append_gzip_tar(command, archive, name, Path(os.environ['DEB_TEST_PIGZ']).resolve())
            parities = set()
            with archive.open('rb') as stream:
                self.assertEqual(stream.read(8), b'!<arch>\n')
                for name, content in payloads:
                    header = stream.read(60)
                    self.assertEqual(header[:16].decode().rstrip(' /'), name)
                    self.assertEqual(header[58:], b'`\n')
                    self.assertEqual(tuple(int(header[a:b]) for a, b in [(16, 28), (28, 34), (34, 40)]), (0, 0, 0))
                    self.assertEqual(int(header[40:48], 8), 0o644)
                    size = int(header[48:58])
                    self.assertEqual(gzip.decompress(stream.read(size)), content)
                    parities.add(size % 2)
                    if size % 2:
                        self.assertEqual(stream.read(1), b'\n')
                self.assertEqual(stream.read(), b'')
            self.assertEqual(parities, {0, 1})


class ControlTest(unittest.TestCase):
    def test_package_architecture_must_match_target_platform(self):
        for architecture, target in [('amd64', 'arm64'), ('arm64', 'amd64')]:
            with self.subTest(architecture=architecture):
                fields = ['Package=example', 'Version=1.0', 'Architecture=' + architecture]
                with self.assertRaisesRegex(ValueError, 'control architecture differs from target platform'):
                    assemble(SimpleNamespace(control=None, field=fields, target_architecture=target))

    def test_finalized_control_requires_descriptive_fields(self):
        # Compatibility for the scalar API must not relax finalized controls.
        fields = {'Package': 'example', 'Version': '1.0', 'Architecture': 'amd64',
                  'Maintainer': 'Example <example@example.invalid>',
                  'Description': 'Finalized control'}
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary) / 'control'
            for missing in ('Maintainer', 'Description'):
                with self.subTest(missing=missing):
                    control.write_text(''.join(key + ': ' + value + '\n'
                                               for key, value in fields.items() if key != missing))
                    with self.assertRaisesRegex(ValueError, 'control lacks required field: ' + missing):
                        assemble(SimpleNamespace(control=control, field=[]))


if __name__ == '__main__':
    unittest.main()
