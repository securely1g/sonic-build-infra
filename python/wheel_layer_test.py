import base64
import csv
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from wheel_layer_builder import build_layer


class WheelLayerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wheel(self, additions=None):
        files = {
            "sample/__init__.py": b"value = 42\n",
            "sample/data.json": b'{"example": true}\n',
            "sample-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: sample\nVersion: 1.0\n",
            "sample-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            "sample-1.0.dist-info/entry_points.txt": b"[console_scripts]\nsample = sample.cli:main\n",
            "sample-1.0.data/scripts/helper": b"#!python\nprint('helper')\n",
            "sample-1.0.data/data/share/sample/example.txt": b"shared data\n",
        }
        files.update(additions or {})
        records = io.StringIO()
        writer = csv.writer(records, lineterminator="\n")
        for name, content in sorted(files.items()):
            hash_ = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
            writer.writerow([name, "sha256=" + hash_, len(content)])
        writer.writerow(["sample-1.0.dist-info/RECORD", "", ""])
        files["sample-1.0.dist-info/RECORD"] = records.getvalue().encode()
        path = self.root / "sample-1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o100755 if name.endswith("/helper") else 0o100644) << 16
                archive.writestr(info, content)
        return path

    def layer(self, wheels, output="layer.tar"):
        destination = self.root / output
        build_layer(wheels, destination, "/usr/local/lib/python3.13/dist-packages",
                    "/usr/local/bin", "/usr/local/include", "/usr/local", "/usr/bin/python3")
        return destination

    def test_layout_metadata_data_and_entry_points(self):
        output = self.layer([self.wheel()])
        with tarfile.open(output) as archive:
            members = {member.name: member for member in archive.getmembers()}
            site = "./usr/local/lib/python3.13/dist-packages/"
            self.assertEqual(archive.extractfile(site + "sample/data.json").read(), b'{"example": true}\n')
            self.assertEqual(archive.extractfile("./usr/local/share/sample/example.txt").read(), b"shared data\n")
            entrypoint = archive.extractfile("./usr/local/bin/sample").read()
            self.assertTrue(entrypoint.startswith(b"#!/usr/bin/python3\n"))
            self.assertIn(b"from sample.cli import main", entrypoint)
            self.assertTrue(archive.extractfile("./usr/local/bin/helper").read().startswith(b"#!/usr/bin/python3\n"))
            self.assertEqual(members["./usr/local/bin/sample"].mode, 0o755)
            self.assertEqual(members["./usr/local/bin/helper"].mode, 0o755)
            self.assertEqual(members[site + "sample/data.json"].mode, 0o644)
            record = archive.extractfile(site + "sample-1.0.dist-info/RECORD").read()
            self.assertIn(b"../../../bin/sample,sha256=", record)
            self.assertIn(site + "sample-1.0.dist-info/INSTALLER", members)
            for member in members.values():
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
                self.assertNotIn("__pycache__", member.name)

    def test_reproducible_output(self):
        wheel = self.wheel()
        first = self.layer([wheel], "first.tar").read_bytes()
        second = self.layer([wheel], "second.tar").read_bytes()
        self.assertEqual(first, second)

    def test_conflicting_wheels_fail(self):
        wheel = self.wheel()
        with self.assertRaises(FileExistsError):
            self.layer([wheel, wheel])

    def test_invalid_record_is_rejected(self):
        wheel = self.wheel()
        with zipfile.ZipFile(wheel) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files["sample/data.json"] = b"changed"
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        with self.assertRaises(ValueError):
            self.layer([wheel])

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.layer([self.wheel({"../escape": b"no"})])

    def test_entrypoint_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.layer([self.wheel({"sample-1.0.dist-info/entry_points.txt": b"[console_scripts]\n../../escape = sample:main\n"})])


if __name__ == "__main__":
    unittest.main()
