"""Exercise the staged Debian packager with its declared packaging tools."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
import unittest

from python.runfiles import runfiles


class BuildDebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resolver = runfiles.Create()
        if resolver is None:
            raise RuntimeError("Bazel runfiles are required for the declared packaging tools")
        cls.tools = {}
        for name in ("build_deb", "dpkg_deb"):
            location = resolver.Rlocation(getattr(TOOL_ARGS, name))
            if not location or not Path(location).is_file():
                raise RuntimeError(f"Missing declared {name} tool in runfiles: {location}")
            # Preserve launcher symlinks and their adjacent runfiles trees.
            cls.tools[name] = str(Path(location).absolute())
        cls.runfiles_env = resolver.EnvVars()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sonic-build-deb-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.control = self.root / "control"
        self.control.write_text(
            "Package: sonic-deb-regression\n"
            "Version: 1.2.3\n"
            "Architecture: all\n"
            "Maintainer: SONiC <sonic@example.org>\n"
            "Depends: libc6 (>= 2.36)\n"
            "Description: isolated Debian packaging regression\n"
            " Payload assembled from a deployment archive.\n"
        )

    def member(self, name, content=None, *, kind=tarfile.REGTYPE, mode=0o644, link=""):
        member = tarfile.TarInfo(name)
        member.type = kind
        member.mode = mode
        # The package must get root ownership without needing a root build.
        member.uid, member.gid = 1234, 2345
        member.mtime = 123456789
        member.linkname = link
        member.size = len(content) if content is not None else 0
        return member, content

    def archive(self, name, entries):
        path = self.root / name
        with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
            for member, content in entries:
                archive.addfile(member, io.BytesIO(content) if content is not None else None)
        return path

    def command(self, args, *, extra_env=None, check=True):
        environment = os.environ.copy()
        environment.update(self.runfiles_env)
        environment.update(extra_env or {})
        result = subprocess.run(
            args,
            cwd=self.root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check:
            self.assertEqual(
                result.returncode,
                0,
                f"Command failed: {args}\n"
                + result.stdout.decode(errors="replace")
                + result.stderr.decode(errors="replace"),
            )
        return result

    def build(self, payload, *, name="probe.deb", extra_env=None, check=True):
        package = self.root / name
        result = self.command(
            [
                self.tools["build_deb"],
                "--data-tar", str(payload),
                "--control", str(self.control),
                "--dpkg-deb", self.tools["dpkg_deb"],
                "--output", str(package),
            ],
            extra_env=extra_env,
            check=check,
        )
        return package, result

    def package_tar(self, package, *, control=False):
        result = self.command([
            self.tools["dpkg_deb"],
            "--ctrl-tarfile" if control else "--fsys-tarfile",
            str(package),
        ])
        return tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:")

    def extract(self, package):
        destination = self.root / (package.stem + "-extracted")
        self.command([self.tools["dpkg_deb"], "--extract", str(package), str(destination)])
        return destination

    def test_missing_and_late_parent_directories_are_installable(self):
        header = "usr/include/dash_api/types.pb.h"
        debug = "usr/lib/debug/.build-id/ab/cdef.debug"
        payload = self.archive("payload.tar", [
            self.member("./" + header, b"header\0bytes", mode=0o640),
            self.member(debug, b"detached symbols"),
            self.member("usr/lib/debug/.build-id", kind=tarfile.DIRTYPE, mode=0o750),
            self.member("usr/lib/debug", kind=tarfile.DIRTYPE, mode=0o755),
        ])
        package, _ = self.build(payload)

        with self.package_tar(package) as archive:
            seen = set()
            for member in archive:
                name = str(PurePosixPath(member.name))
                if name != ".":
                    self.assertIn(str(PurePosixPath(name).parent), seen, member.name)
                self.assertEqual((member.uid, member.gid), (0, 0), member.name)
                seen.add(name)
            self.assertTrue({".", "usr", "usr/include", "usr/include/dash_api"} <= seen)

        installed = self.extract(package)
        self.assertEqual((installed / header).read_bytes(), b"header\0bytes")
        self.assertEqual((installed / header).stat().st_mode & 0o7777, 0o640)
        self.assertEqual((installed / debug).read_bytes(), b"detached symbols")
        self.assertEqual((installed / "usr/lib/debug/.build-id").stat().st_mode & 0o7777, 0o750)
        self.assertEqual((installed / "usr/include/dash_api").stat().st_mode & 0o7777, 0o755)

    def test_symlinks_and_forward_hardlinks_preserve_installed_behavior(self):
        executable = b"#!/bin/sh\nexit 0\n"
        payload = self.archive("links.tar", [
            self.member("usr/bin/alias", kind=tarfile.LNKTYPE, mode=0o751, link="./usr/lib/payload"),
            self.member("usr/lib/libsample.so", kind=tarfile.SYMTYPE, mode=0o777, link="libsample.so.1"),
            self.member("usr/lib/absolute.so", kind=tarfile.SYMTYPE, mode=0o777, link="/usr/lib/libsample.so.1"),
            self.member("usr/lib/payload", executable, mode=0o751),
            self.member("usr/lib/libsample.so.1", b"shared\0library", mode=0o640),
        ])
        package, _ = self.build(payload)
        installed = self.extract(package)

        original, alias = installed / "usr/lib/payload", installed / "usr/bin/alias"
        self.assertEqual(original.read_bytes(), executable)
        self.assertEqual(alias.read_bytes(), executable)
        self.assertEqual(original.stat().st_ino, alias.stat().st_ino)
        self.assertEqual(alias.stat().st_mode & 0o7777, 0o751)
        self.assertEqual((installed / "usr/lib/libsample.so").readlink(), Path("libsample.so.1"))
        self.assertEqual((installed / "usr/lib/libsample.so").read_bytes(), b"shared\0library")
        self.assertEqual((installed / "usr/lib/absolute.so").readlink(), Path("/usr/lib/libsample.so.1"))

    def test_sparse_archive_keeps_logical_file_contents(self):
        # GNU sparse 0.1 stores only two nonzero extents in a PAX archive.
        # Construct it directly so generating this fixture needs no host tar.
        member, content = self.member("usr/share/probe/sparse", b"ab", mode=0o600)
        member.pax_headers = {
            "GNU.sparse.map": "0,1,1048576,1",
            "GNU.sparse.size": "1048577",
        }
        payload = self.archive("sparse.tar", [(member, content)])
        with tarfile.open(payload) as archive:
            self.assertEqual(archive.getmember(member.name).sparse, [(0, 1), (1048576, 1)])

        package, _ = self.build(payload)
        installed = self.extract(package)
        sparse = installed / member.name
        self.assertEqual(sparse.read_bytes(), b"a" + b"\0" * (1024 * 1024 - 1) + b"b")
        self.assertEqual(sparse.stat().st_mode & 0o7777, 0o600)

    def test_control_and_checksums_describe_the_installed_payload(self):
        contents = {
            "usr/bin/probe": b"#!/bin/sh\nexit 0\n",
            "usr/share/probe/name with spaces": b"data\0bytes\n",
        }
        payload = self.archive("checksums.tar", [
            *(self.member(name, content) for name, content in contents.items()),
            self.member("usr/bin/probe-alias", kind=tarfile.LNKTYPE, link="usr/bin/probe"),
            self.member("usr/bin/probe-link", kind=tarfile.SYMTYPE, link="probe"),
        ])
        package, _ = self.build(payload)
        with self.package_tar(package, control=True) as archive:
            members = {str(PurePosixPath(member.name)): member for member in archive}
            self.assertEqual(archive.extractfile(members["control"]).read(), self.control.read_bytes())
            sums = archive.extractfile(members["md5sums"]).read().decode()
        checksums = dict(line.split("  ", 1)[::-1] for line in sums.splitlines())
        expected = {
            name: hashlib.md5(content).hexdigest()
            for name, content in {**contents, "usr/bin/probe-alias": contents["usr/bin/probe"]}.items()
        }
        self.assertEqual(checksums, expected)
        info = self.command([self.tools["dpkg_deb"], "--field", str(package)]).stdout.decode()
        self.assertIn("Architecture: all\n", info)
        self.assertIn("Depends: libc6 (>= 2.36)\n", info)

    def test_equivalent_trees_produce_identical_debs(self):
        entries = [
            self.member("usr/share/probe/b", b"second"),
            self.member("usr/share/probe/a", b"first", mode=0o640),
            self.member("usr/share/probe", kind=tarfile.DIRTYPE, mode=0o750),
        ]
        first_input = self.archive("first.tar", entries)
        # Filesystem staging and dpkg should make archive entry order irrelevant.
        for member, _ in entries:
            member.mtime += 1000
        second_input = self.archive("second.tar", list(reversed(entries)))
        first, _ = self.build(first_input, name="first.deb", extra_env={"SOURCE_DATE_EPOCH": "1"})
        second, _ = self.build(second_input, name="second.deb", extra_env={"SOURCE_DATE_EPOCH": "2000000000"})
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.package_tar(first) as archive:
            self.assertTrue(all(member.mtime == 0 for member in archive))

    def test_invalid_or_ambiguous_payload_paths_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"unchanged")
        cases = [
            [self.member("../escape", b"payload")],
            [self.member(str(sentinel), b"overwrite")],
            [self.member("usr/a", b"first"), self.member("./usr/a", b"second")],
            [self.member("usr", b"not a directory"), self.member("usr/a", b"payload")],
            [self.member("DEBIAN/control", b"Package: injected\n")],
            [self.member("a", kind=tarfile.LNKTYPE, link="../escape")],
            [self.member("a", kind=tarfile.LNKTYPE, link="missing")],
            [self.member("a", kind=tarfile.LNKTYPE, link="b"), self.member("b", kind=tarfile.LNKTYPE, link="a")],
            [self.member("escape", kind=tarfile.SYMTYPE, link=str(outside)), self.member("escape/sentinel", b"overwrite")],
        ]
        for number, entries in enumerate(cases):
            with self.subTest(paths=[member.name for member, _ in entries]):
                payload = self.archive(f"invalid-{number}.tar", entries)
                package, result = self.build(payload, name=f"invalid-{number}.deb", check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
                self.assertFalse(package.exists(), "A rejected payload must not produce a package")
                self.assertEqual(sentinel.read_bytes(), b"unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-deb", required=True)
    parser.add_argument("--dpkg-deb", required=True)
    TOOL_ARGS, remaining = parser.parse_known_args()
    unittest.main(argv=[__file__, *remaining])
