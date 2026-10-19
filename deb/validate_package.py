"""Validate a produced Debian archive against its declared package inputs."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import subprocess
import tarfile
import tempfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def inventory(path, include_contents=False):
    result, contents = {}, {}
    with tarfile.open(path) as archive:
        for entry in archive:
            name = str(PurePosixPath(entry.name))
            require(not name.startswith("/") and ".." not in PurePosixPath(name).parts,
                    "unsafe archive path: " + name)
            require(name not in result, "duplicate archive path: " + name)
            require(entry.uid == 0 and entry.gid == 0, "non-root package owner: " + name)
            record = {"mode": entry.mode, "uid": entry.uid, "gid": entry.gid}
            if entry.isfile():
                source = archive.extractfile(entry)
                sha256, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
                prefix = source.read(20)
                sha256.update(prefix)
                md5.update(prefix)
                size = len(prefix)
                for part in iter(lambda: source.read(1024 * 1024), b""):
                    sha256.update(part)
                    md5.update(part)
                    size += len(part)
                record.update(kind="file", size=size, sha256=sha256.hexdigest(), md5=md5.hexdigest())
                if prefix.startswith(b"\x7fELF"):
                    require(len(prefix) == 20, "truncated ELF: " + name)
                    order = "<" if prefix[5] == 1 else ">"
                    record["elf_machine"] = struct.unpack(order + "H", prefix[18:20])[0]
                # Control files are small text inputs; retain bytes for exact comparison.
                if include_contents:
                    contents[name] = archive.extractfile(entry).read()
            elif entry.isdir():
                record["kind"] = "directory"
            elif entry.issym() or entry.islnk():
                record.update(kind="symlink" if entry.issym() else "hardlink",
                              linkname=str(PurePosixPath(entry.linkname)))
            else:
                raise ValueError("unsupported archive type: " + name)
            result[name] = record
    return result, contents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("deb", "data", "control", "tar", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ("package", "version", "architecture"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--control-file", action="append", default=[])
    args = parser.parse_args()
    extra = {}
    for value in args.control_file:
        name, path = value.split("=", 1)
        require(name not in extra and "/" not in name, "duplicate/invalid control name")
        extra[name] = Path(path)
    with tempfile.TemporaryDirectory(prefix="debian-archive-check-") as temporary:
        root = Path(temporary)
        members = subprocess.check_output([str(args.tar), "-tf", str(args.deb)], text=True).splitlines()
        require(members == ["debian-binary", "control.tar.gz", "data.tar.gz"],
                "unexpected Debian archive members or order: " + repr(members))
        require(subprocess.check_output([str(args.tar), "-xOf", str(args.deb), "debian-binary"]) == b"2.0\n",
                "invalid Debian archive version")
        for member in members[1:]:
            with (root / member).open("wb") as destination:
                subprocess.run([str(args.tar), "-xOf", str(args.deb), member], stdout=destination, check=True)
        expected, _ = inventory(args.data)
        actual, _ = inventory(root / "data.tar.gz")
        require(actual == expected, "DEB data differs from its declared payload")
        metadata, text = inventory(root / "control.tar.gz", include_contents=True)
        files = {name for name, value in metadata.items() if value["kind"] != "directory"}
        require(files == {"control", "md5sums", *extra}, "unexpected or missing control entries")
        require(text["control"] == args.control.read_bytes(), "DEB control differs from generated control")
        fields = dict(line.split(": ", 1) for line in text["control"].decode().splitlines()
                      if line and not line.startswith(" ") and ": " in line)
        for name, expected_value in (("Package", args.package), ("Version", args.version), ("Architecture", args.architecture)):
            require(fields.get(name) == expected_value, "wrong " + name + " in produced DEB")
        scripts = {"preinst", "postinst", "prerm", "postrm", "config"}
        for name, path in extra.items():
            require(text[name] == path.read_bytes(), "packaged control input differs: " + name)
        for name in files:
            require(metadata[name]["kind"] == "file" and metadata[name]["mode"] == (0o755 if name in scripts else 0o644),
                    "incorrect control type/mode: " + name)
        checksums = {}
        for line in text["md5sums"].decode().splitlines():
            digest, name = line.split(None, 1)
            name = str(PurePosixPath(name))
            require(name not in checksums, "duplicate md5sums entry")
            checksums[name] = digest
        require(checksums == {name: value["md5"] for name, value in actual.items() if value["kind"] == "file"},
                "md5sums do not describe the actual regular files")
        machine = {"amd64": 62, "arm64": 183, "armhf": 40}[args.architecture]
        require(all(value.get("elf_machine", machine) == machine for value in actual.values()), "wrong ELF architecture")
        if "conffiles" in text:
            paths = text["conffiles"].decode().splitlines()
            require(len(paths) == len(set(paths)), "duplicate conffile")
            for path in paths:
                require(path.startswith("/") and actual.get(path[1:], {}).get("kind") == "file", "conffile is not packaged: " + path)
    args.output.write_text(json.dumps({
        "_generated": "AUTO-GENERATED by deb/validate_package.py. DO NOT EDIT MANUALLY.",
        "package": args.package, "version": args.version, "architecture": args.architecture,
        "deb_sha256": sha(args.deb), "data_sha256": sha(args.data), "control_sha256": sha(args.control),
        "payload": actual, "control_files": metadata, "status": "passed",
    }, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
