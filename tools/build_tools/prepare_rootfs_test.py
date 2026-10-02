"""Check package metadata and archive boundaries without creating DEBs."""

import io
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

from prepare_rootfs import initialize_root, package_metadata, preserve_empty_directories, validate_links
from prepare_runtime import extract_payload


def write_tar(path, records):
    with tarfile.open(path, "w") as archive:
        for name, data in records.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


class RootfsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "root"
        initialize_root(self.root)

    def test_merged_usr_preserves_absolute_path_tools(self):
        payload = self.directory / "data.tar"
        write_tar(payload, {"./bin/tool": b"tool", "./usr/lib/library": b"library"})
        extract_payload(self.root, payload)
        self.assertEqual((self.root / "usr/bin/tool").read_bytes(), b"tool")
        self.assertEqual((self.root / "lib/library").read_bytes(), b"library")

    def test_file_only_cache_roundtrip_preserves_empty_directory_links(self):
        # Model the cache downloader creating file/symlink parents but omitting
        # DirectoryNodes that contain no files. The real merged-/usr aliases
        # must still resolve, as must an alias to a nested empty directory.
        (self.root / "var/cache/example/empty").mkdir(parents=True)
        (self.root / "empty-cache").symlink_to("var/cache/example/empty")
        (self.root / "usr/bin/tool").write_bytes(b"tool")

        def roundtrip(destination):
            destination.mkdir()
            for directory, directories, files in os.walk(self.root, followlinks=False):
                for name in directories + files:
                    source = Path(directory) / name
                    target = destination / source.relative_to(self.root)
                    if source.is_symlink():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.symlink_to(source.readlink())
                    elif source.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)

        unpreserved = self.directory / "unpreserved"
        roundtrip(unpreserved)
        with self.assertRaisesRegex(ValueError, "unresolved runtime"):
            validate_links(unpreserved)

        preserve_empty_directories(self.root)
        preserved = self.directory / "preserved"
        roundtrip(preserved)
        validate_links(preserved)
        original_directories = {
            Path(directory).relative_to(self.root)
            for directory, _, _ in os.walk(self.root, followlinks=False)
        }
        cached_directories = {
            Path(directory).relative_to(preserved)
            for directory, _, _ in os.walk(preserved, followlinks=False)
        }
        self.assertEqual(cached_directories, original_directories)
        self.assertEqual((preserved / "usr/bin/tool").read_bytes(), b"tool")
        self.assertFalse((preserved / "usr/bin/.bazel-keep-directory").exists())
        for alias in ("lib32", "libx32", "empty-cache"):
            self.assertTrue((preserved / alias).is_dir())
            self.assertEqual((preserved / alias / ".bazel-keep-directory").read_bytes(), b"")

    def test_library_metadata_preserves_package_ownership(self):
        payload = self.directory / "data.tar"
        control = self.directory / "control.tar"
        write_tar(payload, {"./usr/lib/libsample.so.1": b"library"})
        write_tar(control, {
            "./control": b"Package: libsample\nVersion: 1.2\nArchitecture: amd64\nMulti-Arch: same\n",
            "./shlibs": b"libsample 1 libsample (>= 1.2)\n",
            "./postinst": b"must not execute",
        })
        package_metadata(self.root, payload, control)
        info = self.root / "var/lib/dpkg/info"
        self.assertEqual((info / "format").read_text(), "1\n")
        self.assertEqual((info / "libsample:amd64.list").read_text(), "/usr/lib/libsample.so.1\n")
        self.assertEqual((info / "libsample:amd64.shlibs").read_text(), "libsample 1 libsample (>= 1.2)\n")
        self.assertFalse((info / "libsample:amd64.postinst").exists())

    def test_archive_path_escape_is_rejected(self):
        payload = self.directory / "bad.tar"
        write_tar(payload, {"../outside": b"bad"})
        with self.assertRaises(ValueError):
            extract_payload(self.root, payload)
        self.assertFalse((self.directory / "outside").exists())

    def test_symlink_escape_is_rejected(self):
        (self.root / "usr/bin/unsafe").symlink_to("../../../../outside")
        with self.assertRaisesRegex(ValueError, "escapes output"):
            validate_links(self.root)

    def test_deferred_hardlink_parent_replacement_is_rejected(self):
        (self.root / "inside").mkdir()
        outside = self.directory / "outside"
        outside.mkdir()
        payload = self.directory / "bad-hardlink.tar"
        with tarfile.open(payload, "w") as archive:
            source = tarfile.TarInfo("safe")
            source.size = 4
            archive.addfile(source, io.BytesIO(b"safe"))
            parent = tarfile.TarInfo("a")
            parent.type = tarfile.SYMTYPE
            parent.linkname = "inside"
            archive.addfile(parent)
            link = tarfile.TarInfo("a/escaped")
            link.type = tarfile.LNKTYPE
            link.linkname = "safe"
            archive.addfile(link)
            parent.linkname = "../outside"
            archive.addfile(parent)
        with self.assertRaises(ValueError):
            extract_payload(self.root, payload)
        self.assertFalse((outside / "escaped").exists())

    def test_unresolved_executable_is_rejected(self):
        (self.root / "usr/bin/missing").symlink_to("absent")
        with self.assertRaisesRegex(ValueError, "unresolved runtime"):
            validate_links(self.root)

    def test_unresolved_manpage_does_not_require_an_unrelated_package(self):
        directory = self.root / "usr/share/man/man1"
        directory.mkdir(parents=True)
        missing = directory / "optional.1"
        missing.symlink_to("missing.1")
        validate_links(self.root)
        self.assertFalse(missing.is_symlink())


if __name__ == "__main__":
    unittest.main()
