"""Check default timestamps through both public packaging entry points."""

import sys
import tarfile


plain, deployed, debug, custom = sys.argv[1:]
for path, expected in (
    (plain, {"usr/bin/file.txt": 1672560000}),
    (deployed, {"usr/bin/hello": 1672560000, "usr/share/file.txt": 1672560000, "usr/bin/hello-link": 0, "usr": 0}),
    (custom, {"usr/bin/hello": 123}),
):
    with tarfile.open(path) as archive:
        entries = {entry.name.removeprefix("./"): entry for entry in archive}
        for name, timestamp in expected.items():
            assert entries[name].mtime == timestamp, (name, entries[name].mtime)
        if path == deployed:
            assert archive.extractfile(entries["usr/bin/hello"]).read().startswith(b"\x7fELF")
            assert entries["usr/bin/hello-link"].issym()
            assert entries["usr/bin/hello-link"].linkname == "hello"
with tarfile.open(debug) as archive:
    symbols = [entry for entry in archive if entry.isfile()]
    assert symbols and all(entry.mtime == 1672560000 for entry in symbols)
    assert all(archive.extractfile(entry).read().startswith(b"\x7fELF") for entry in symbols)
