"""Compare real bsdtar outputs after changing only input filesystem timestamps."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


bsdtar, fixed_manifest, raw_manifest = [Path(arg).resolve() for arg in sys.argv[1:]]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    payload = root / "payload"
    payload.write_bytes(b"identical package payload\n")
    archives = {}
    for kind, manifest in (("fixed", fixed_manifest), ("raw", raw_manifest)):
        for timestamp in (123456789, 987654321):
            os.utime(payload, (timestamp, timestamp))
            output = root / f"{kind}-{timestamp}.tar"
            subprocess.run(
                [str(bsdtar), "--create", "--format=pax", "--file", str(output), "@" + str(manifest)],
                cwd=root,
                check=True,
            )
            archives[kind, timestamp] = output
    assert archives["raw", 123456789].read_bytes() != archives["raw", 987654321].read_bytes(), "old missing-time manifest did not reproduce timestamp drift"
    assert archives["fixed", 123456789].read_bytes() == archives["fixed", 987654321].read_bytes(), "normalized archives differ"
    with tarfile.open(archives["fixed", 123456789]) as archive:
        entries = {member.name.removeprefix("./"): member for member in archive}
        assert entries["usr/bin/file"].mtime == 1672560000
        assert entries["escaped name"].mtime == 0
        assert entries["link"].mtime == 0
        assert entries["link"].issym() and entries["link"].linkname == "usr/bin/file"
        assert (entries["fixed"].mtime, entries["fixed"].uid, entries["fixed"].gid, entries["fixed"].mode) == (123, 42, 43, 0o751)
        assert entries["usr/bin/file"].mode == 0o640
        for name in ("usr/bin/file", "escaped name", "fixed"):
            assert archive.extractfile(entries[name]).read() == payload.read_bytes()
