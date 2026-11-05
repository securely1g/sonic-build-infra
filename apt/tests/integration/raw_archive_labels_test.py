"""Original raw archive labels expose the unmodified data member of each DEB.

The compatibility contract is the former public data.tar.xz input, including its
bytes. Runfiles must contain it through the same source label; a normalized TAR
or a private source target cannot satisfy the contract.
"""
from pathlib import Path
import sys
import unittest

from debian.arfile import ArFile


class RawArchiveLabelsTest(unittest.TestCase):
    def test_original_data_members_are_public_and_unchanged(self):
        arguments = sys.argv[1:]
        self.assertTrue(arguments and len(arguments) % 2 == 0)
        for payload, package in zip(arguments[::2], arguments[1::2]):
            with self.subTest(package=package):
                archive = ArFile(package)
                self.assertEqual(Path(payload).read_bytes(), archive.getmember('data.tar.xz').read())


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]])
