"""Debian deb(5) permits GNU long names/links and rejects PAX typeflags.

Regression: the kernel debug DEB failed dpkg unpack on a PAX 'x' header.
This reads the package produced by sonic_deb, including both final archives.
"""
import io
from pathlib import Path
import sys
import tarfile
import unittest

from debian.debfile import DebFile


PACKAGE = Path(sys.argv.pop(1))


class ArchiveFormatTest(unittest.TestCase):
    def test_final_archives_use_debian_supported_headers(self):
        with DebFile(str(PACKAGE)) as package:
            for part in (package.control, package.data):
                archive = part.tgz()
                stream = archive.fileobj
                stream.seek(0)
                # Inspect raw records: tarfile hides GNU/PAX extension headers.
                while True:
                    header = stream.read(512)
                    if not header or header == bytes(512):
                        break
                    self.assertEqual(len(header), 512)
                    self.assertIn(header[156:157], (b'\0', b'0', b'1', b'2', b'3', b'4', b'5', b'6', b'L', b'K'))
                    size = tarfile.nti(header[124:136])
                    stream.seek((size + 511) // 512 * 512, io.SEEK_CUR)

    def test_long_file_and_symlink_preserve_payload_metadata(self):
        name = 'usr/lib/debug/' + 'kernel-component-' * 8 + '.debug'
        with DebFile(str(PACKAGE)) as package:
            archive = package.data.tgz()
            entries = {member.name.removeprefix('./'): member for member in archive}
            member = entries[name]
            self.assertEqual((member.uid, member.gid, member.mode), (123, 456, 0o640))
            self.assertEqual(archive.extractfile(member).read(), b'Debian archive format contract payload.\n')
            link = entries['usr/lib/debug/current']
            self.assertTrue(link.issym())
            self.assertEqual(link.linkname, name.rsplit('/', 1)[1])


if __name__ == '__main__':
    unittest.main()
