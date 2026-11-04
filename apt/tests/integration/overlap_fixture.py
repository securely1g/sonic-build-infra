"""Make two TAR/control fixtures with shared directories; never produce DEBs."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

out = Path(sys.argv[1])
packages = {}
for short in ("a", "b"):
    name = "apt-overlap-" + short
    data = out / ("overlap_" + short + ".data.tar")
    control = out / ("overlap_" + short + ".control.tar")
    with tarfile.open(data, "w", format=tarfile.USTAR_FORMAT) as archive:
        for directory in (".", "usr", "usr/share", "usr/share/overlap"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        content = ("unchanged payload " + short + "\n").encode()
        info = tarfile.TarInfo("usr/share/overlap/" + short)
        info.mode = 0o640 if short == "a" else 0o755
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
        link = tarfile.TarInfo("usr/share/overlap/" + short + "-link")
        link.mode = 0o777 if short == "a" else 0o755
        link.type = tarfile.SYMTYPE if short == "a" else tarfile.LNKTYPE
        link.linkname = "a" if short == "a" else "usr/share/overlap/b"
        archive.addfile(link)
        # Later non-ELF configuration overrides are valid TAR semantics.
        content = ("configuration from package " + short + "\n").encode()
        info = tarfile.TarInfo("usr/share/overlap/config.txt")
        info.mode = 0o640 if short == "a" else 0o600
        info.mtime = 1 if short == "a" else 2
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    text = ("Package: " + name + "\nVersion: 1.0\nArchitecture: all\nDescription: TAR-only fixture\n").encode()
    with tarfile.open(control, "w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("control")
        info.mode = 0o644
        info.size = len(text)
        archive.addfile(info, io.BytesIO(text))
    item = {"name": name, "version": "1.0", "architecture": "all", "sha256": "0" * 64, "depends_on": []}
    for kind, path in (("payload", data), ("control", control)):
        item[kind + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        item[kind + "_size"] = path.stat().st_size
    packages["/test/" + name + ":all=1.0"] = item
lock = {"version": 2, "dependency_sets": {"dictionary": {"sets": {arch: {
    key.rsplit("=", 1)[0]: "1.0" for key in packages} for arch in ("amd64", "arm64")}}}, "packages": packages}
(out / "overlap.lock.json").write_text(json.dumps(lock))
