"""Exercise dpkg unpacking of sparse and misordered deployment tar trees."""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from normalize_data_tar import normalize


class NormalizeDataTarTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sonic-deb-normalize-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, name, entries):
        path = self.root / name
        with tarfile.open(path, "w") as archive:
            for member, content in entries:
                archive.addfile(member, io.BytesIO(content) if content is not None else None)
        return path

    def member(self, name, content=None, kind=tarfile.REGTYPE, mode=0o644, link=""):
        member = tarfile.TarInfo(name)
        member.type = kind
        member.mode = mode
        member.uid = member.gid = 0
        member.mtime = 1234
        member.linkname = link
        member.size = len(content) if content is not None else 0
        return member, content

    def normalized(self, entries):
        original = self.archive("input.tar", entries)
        output = self.root / "data.tar"
        normalize(str(original), str(output))
        return output

    def assert_parents_first(self, path):
        with tarfile.open(path) as archive:
            seen = set()
            for member in archive:
                name = Path(member.name).as_posix()
                if name != ".":
                    self.assertIn(str(Path(name).parent), seen, name)
                seen.add(name)

    def test_sparse_runtime_and_out_of_order_debug_directories(self):
        debug = "./usr/lib/debug/.build-id/ab/cdef.debug"
        output = self.normalized([
            self.member("./usr/include/dash_api/types.pb.h", b"header\0bytes", mode=0o640),
            self.member(debug, b"detached symbols"),
            self.member("./usr/lib/debug/.build-id", kind=tarfile.DIRTYPE, mode=0o750),
            self.member("./usr/lib/debug", kind=tarfile.DIRTYPE, mode=0o755),
        ])
        self.assert_parents_first(output)
        with tarfile.open(output) as archive:
            members = {member.name.removeprefix("./"): member for member in archive}
            self.assertEqual(members["usr/lib/debug/.build-id"].mode, 0o750)
            self.assertEqual(members["usr/lib/debug/.build-id"].mtime, 1234)
            self.assertEqual(members["usr/include/dash_api/types.pb.h"].mode, 0o640)
            self.assertEqual(archive.extractfile(members["usr/include/dash_api/types.pb.h"]).read(), b"header\0bytes")
            self.assertEqual(archive.extractfile(members[debug.removeprefix("./")]).read(), b"detached symbols")
        second = self.root / "second.tar"
        normalize(str(output), str(second))
        self.assertEqual(output.read_bytes(), second.read_bytes())

    def test_links_and_regular_payloads_are_preserved(self):
        output = self.normalized([
            self.member("./usr/bin/hardlink", kind=tarfile.LNKTYPE, mode=0o751, link="./usr/lib/payload"),
            self.member("./usr/lib/libsample.so", kind=tarfile.SYMTYPE, mode=0o777, link="libsample.so.1"),
            self.member("./usr/lib/payload", b"executable\0contents", mode=0o751),
            self.member("./usr/lib/libsample.so.1", b"shared library", mode=0o640),
        ])
        self.assert_parents_first(output)
        with tarfile.open(output) as archive:
            members = {member.name: member for member in archive}
            self.assertLess(list(members).index("./usr/lib/payload"), list(members).index("./usr/bin/hardlink"))
            self.assertEqual(members["./usr/lib/libsample.so"].linkname, "libsample.so.1")
            self.assertTrue(members["./usr/lib/libsample.so"].issym())
            self.assertEqual(members["./usr/lib/libsample.so"].mode, 0o777)
            self.assertEqual(members["./usr/bin/hardlink"].linkname, "./usr/lib/payload")
            self.assertTrue(members["./usr/bin/hardlink"].islnk())
            self.assertEqual(archive.extractfile(members["./usr/bin/hardlink"]).read(), b"executable\0contents")

    def test_deb_payload_preserves_install_tree_and_links(self):
        output = self.normalized([
            self.member("./usr/include/dash_api/types.pb.h", b"header", mode=0o640),
            self.member("./usr/lib/libsample.so", kind=tarfile.SYMTYPE, mode=0o777, link="libsample.so.1"),
            self.member("./usr/lib/libsample.so.1", b"library", mode=0o644),
            self.member("./usr/lib/debug/.build-id/ab/cdef.debug", b"debug symbols"),
            self.member("./usr/lib/debug/.build-id", kind=tarfile.DIRTYPE, mode=0o755),
        ])
        control = (
            "Package: sonic-normalize-regression\nVersion: 1.0\nArchitecture: all\n"
            "Maintainer: SONiC\nDescription: isolated payload ordering regression\n"
        ).encode()
        control_tar = self.archive("control.tar", [self.member("./control", control)])
        debian_binary = self.root / "debian-binary"
        debian_binary.write_text("2.0\n")
        package = self.root / "probe.deb"
        subprocess.run(["ar", "rc", str(package), str(debian_binary), str(control_tar), str(output)], check=True, capture_output=True)
        install_root = self.root / "installation"
        install_root.mkdir()
        # Inspect/extract the actual Debian package without invoking host dpkg
        # database hooks or requiring root. The separate native installation CI
        # additionally uses apt/dpkg in its clean, disposable container.
        result = subprocess.run([
            "dpkg-deb", "--extract", str(package), str(install_root),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((install_root / "usr/include/dash_api/types.pb.h").read_bytes(), b"header")
        self.assertEqual((install_root / "usr/include/dash_api/types.pb.h").stat().st_mode & 0o777, 0o640)
        self.assertEqual((install_root / "usr/lib/debug/.build-id/ab/cdef.debug").read_bytes(), b"debug symbols")
        self.assertEqual((install_root / "usr/lib/libsample.so").readlink(), Path("libsample.so.1"))
        self.assertEqual((install_root / "usr/lib/libsample.so").read_bytes(), b"library")

    def test_gnu_sparse_file_becomes_valid_dense_payload(self):
        sparse = self.root / "sparse"
        with sparse.open("wb") as stream:
            stream.write(b"a")
            stream.seek(1024 * 1024)
            stream.write(b"b")
        original = self.root / "sparse.tar"
        subprocess.run([
            "tar", "--sparse", "--format=gnu", "-cf", str(original),
            "-C", str(self.root), "sparse",
        ], check=True, capture_output=True)
        with tarfile.open(original) as archive:
            self.assertIsNotNone(archive.getmember("sparse").sparse)
        output = self.root / "dense.tar"
        normalize(str(original), str(output))
        with tarfile.open(output) as archive:
            member = archive.getmember("sparse")
            self.assertEqual(member.type, tarfile.REGTYPE)
            self.assertIsNone(member.sparse)
            self.assertEqual(archive.extractfile(member).read(), sparse.read_bytes())

    def test_rejects_ambiguous_or_unusable_paths(self):
        cases = [
            [self.member("../escape", b"payload")],
            [self.member("usr/a", b"first"), self.member("./usr/a", b"second")],
            [self.member("usr", b"not a directory"), self.member("usr/file", b"payload")],
            [self.member("a", kind=tarfile.LNKTYPE, link="b"), self.member("b", kind=tarfile.LNKTYPE, link="a")],
        ]
        for entries in cases:
            with self.subTest(paths=[member.name for member, _ in entries]):
                with self.assertRaises(ValueError):
                    self.normalized(entries)


if __name__ == "__main__":
    unittest.main()
