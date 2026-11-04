"""Check actual apt_layer output preserves both payloads with unique paths."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import unittest


def inventory(path):
    # Standard flatten may add a harmless ./ prefix to archive link targets.
    # Normalize only dot components; retain relative .. and absolute targets.
    records = []
    with tarfile.open(path) as archive:
        for member in archive:
            records.append((str(PurePosixPath(member.name)), member.type.decode(), member.mode,
                            member.uid, member.gid, member.mtime,
                            str(PurePosixPath(member.linkname)) if member.linkname else "",
                            hashlib.sha256(archive.extractfile(member).read()).hexdigest() if member.isfile() else None))
    return records


class OverlapTest(unittest.TestCase):
    def test_shared_directories_are_unique_and_every_payload_is_preserved(self):
        expected = {}
        for path in INPUTS:
            for record in inventory(path):
                if record[0] in expected:
                    if record[1] == "5":
                        self.assertEqual(record, expected[record[0]])
                    else:
                        self.assertEqual(record[0], "usr/share/overlap/config.txt")
                        self.assertEqual(record[1], "0")
                expected[record[0]] = record
        actual = inventory(LAYER)
        names = [row[0] for row in actual]
        self.assertEqual(len(names), len(set(names)), "OCI layer contains repeated paths")
        self.assertEqual({row[0]: row for row in actual}, expected)
        self.assertEqual({row[1] for row in actual}, {"0", "1", "2", "5"})
        receipt = json.loads(Path(RECEIPT).read_text())
        self.assertEqual([row["package"] for row in receipt["selected"]], ["apt-overlap-a", "apt-overlap-b"])
        self.assertEqual(receipt["skipped_base"], [])

    def test_later_non_elf_override_preserves_its_bytes_and_metadata(self):
        name = "usr/share/overlap/config.txt"
        earlier = dict((row[0], row) for row in inventory(INPUTS[0]))[name]
        later = dict((row[0], row) for row in inventory(INPUTS[1]))[name]
        actual = dict((row[0], row) for row in inventory(LAYER))[name]
        self.assertNotEqual(earlier[-1], later[-1])
        self.assertNotEqual(earlier[2], later[2])
        self.assertNotEqual(earlier[5], later[5])
        self.assertEqual(actual, later)


if __name__ == "__main__":
    *INPUTS, LAYER, RECEIPT = sys.argv[1:]
    unittest.main(argv=[sys.argv[0]])
