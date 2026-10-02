import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from root_owned_tar import root_owned_tar


class OwnershipTest(unittest.TestCase):
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
