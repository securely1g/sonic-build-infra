import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from root_owned_tar import root_owned_tar


class OwnershipTest(unittest.TestCase):
    def test_debug_data_mode_preserves_directory_and_link_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "debug.tar"
            output = Path(directory) / "normalized.tar"
            with tarfile.open(source, "w") as archive:
                for name, kind, mode, link in (
                    ("debug", tarfile.DIRTYPE, 0o755, ""),
                    ("debug/a.debug", tarfile.REGTYPE, 0o755, ""),
                    ("debug/link", tarfile.SYMTYPE, 0o777, "a.debug"),
                ):
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.mode, entry.linkname = kind, mode, link
                    archive.addfile(entry)
            root_owned_tar(str(source), str(output), regular_file_mode=0o644)
            with tarfile.open(output) as archive:
                self.assertEqual(archive.getmember("debug").mode, 0o755)
                self.assertEqual(archive.getmember("debug/a.debug").mode, 0o644)
                self.assertEqual(archive.getmember("debug/link").mode, 0o777)
                self.assertEqual(archive.getmember("debug/link").linkname, "a.debug")

    def test_declared_symlinks_and_file_modes_compose(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.tar"
            output = Path(directory) / "owned.tar"
            with tarfile.open(source, "w") as archive:
                entry = tarfile.TarInfo("usr/lib/debug/a.debug")
                entry.mode = 0o755
                archive.addfile(entry)
            root_owned_tar(str(source), str(output),
                           {"usr/share/doc/example-dbgsym": "example"},
                           regular_file_mode=0o644)
            with tarfile.open(output) as archive:
                self.assertEqual(archive.getmember("usr/lib/debug/a.debug").mode, 0o644)
                link = archive.getmember("./usr/share/doc/example-dbgsym")
                self.assertTrue(link.issym())
                self.assertEqual(link.linkname, "example")
                self.assertEqual((link.uid, link.gid), (0, 0))

    def test_preserves_payload_modes_links_and_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.tar.gz"
            output = Path(directory) / "owned.tar"
            with tarfile.open(source, "w:gz", format=tarfile.PAX_FORMAT) as archive:
                for name, kind, mode, link in (
                    (".", tarfile.DIRTYPE, 0o750, ""),
                    ("data", tarfile.REGTYPE, 0o640, ""),
                    ("executable", tarfile.REGTYPE, 0o751, ""),
                    ("link", tarfile.SYMTYPE, 0o777, "data"),
                    ("hardlink", tarfile.LNKTYPE, 0o640, "data"),
                ):
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.mode, entry.linkname = kind, mode, link
                    entry.uid, entry.gid = 1234, 5678
                    entry.uname, entry.gname = "builder", "builders"
                    entry.mtime = 123456789
                    entry.pax_headers = {"uid": "1234", "gid": "5678", "uname": "builder", "gname": "builders", "comment": "retained"}
                    payload = b"payload" if entry.isfile() else None
                    entry.size = len(payload) if payload else 0
                    archive.addfile(entry, io.BytesIO(payload) if payload else None)
            root_owned_tar(str(source), str(output))
            with tarfile.open(source) as original, tarfile.open(output) as normalized:
                before = original.getmembers()
                after = normalized.getmembers()
                self.assertEqual(len(before), len(after))
                for old, new in zip(before, after):
                    self.assertEqual((new.uid, new.gid, new.uname, new.gname), (0, 0, "", ""))
                    self.assertEqual((old.name, old.type, old.mode, old.size, old.mtime, old.linkname),
                                     (new.name, new.type, new.mode, new.size, new.mtime, new.linkname))
                    self.assertEqual(new.pax_headers, {"comment": "retained"})
                    if new.isfile():
                        self.assertEqual(original.extractfile(old).read(), normalized.extractfile(new).read())


if __name__ == "__main__":
    unittest.main()
