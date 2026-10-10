"""Extract declared Debian inputs without executing package installation code.

The ar reader streams each member, then Python's compression readers preserve
the original decompressed TAR bytes. No host dpkg, ar, tar or filesystem package
installation is needed. An ordered aggregate copies each TAR's original headers
and data, omitting only its end markers before appending the next package.
"""

import argparse
import bz2
import gzip
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_bytes(source, destination, count):
    while count:
        chunk = source.read(min(count, 1024 * 1024))
        require(chunk, "truncated Debian archive")
        destination.write(chunk)
        count -= len(chunk)


def unpack_ar(source, destination):
    """Read the fixed ar format used by deb(5); reject ambiguous package members."""
    members = []
    with source.open("rb") as stream:
        require(stream.read(8) == b"!<arch>\n", "not a Debian ar archive: " + source.name)
        while header := stream.read(60):
            require(len(header) == 60 and header[58:] == b"`\n", "invalid ar member header")
            name = header[:16].decode("ascii").rstrip().removesuffix("/")
            require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name), "unsupported ar member name: " + name)
            size_text = header[48:58].strip()
            require(size_text.isdigit(), "invalid ar member size")
            size = int(size_text)
            require(name not in members, "duplicate Debian archive member: " + name)
            members.append(name)
            with (destination / name).open("wb") as output:
                copy_bytes(stream, output, size)
            if size % 2:
                require(stream.read(1) == b"\n", "invalid ar member padding")
    suffixes = ("", ".gz", ".xz", ".bz2")
    require(len(members) == 3 and members[0] == "debian-binary" and
            members[1] in {"control.tar" + suffix for suffix in suffixes} and
            members[2] in {"data.tar" + suffix for suffix in suffixes},
            "expected debian-binary, control.tar and data.tar, in that order")
    require((destination / "debian-binary").read_bytes() == b"2.0\n", "unsupported Debian archive version")
    return destination / members[1], destination / members[2]


def decompress(source, destination):
    readers = {".tar": open, ".gz": gzip.open, ".xz": lzma.open, ".bz2": bz2.open}
    require(source.suffix in readers, "unsupported Debian compression: " + source.name)
    with readers[source.suffix](source, "rb") as incoming, destination.open("wb") as output:
        shutil.copyfileobj(incoming, output, 1024 * 1024)


def safe_name(value):
    name = PurePosixPath(value)
    require(not name.is_absolute() and ".." not in name.parts, "unsafe archive path: " + value)
    return str(name)


def control_fields(text):
    fields, current = {}, None
    for line in text.splitlines():
        if line.startswith((" ", "\t")):
            require(current is not None, "control continuation has no field")
            fields[current] += "\n" + line
        elif line:
            require(":" in line, "invalid Debian control field: " + line)
            current, value = line.split(":", 1)
            require(re.fullmatch(r"[!-9;-~]+", current) and current.lower() not in
                    {field.lower() for field in fields}, "duplicate or invalid control field: " + current)
            fields[current] = value.lstrip()
    return fields


def inspect_control(path):
    hashes, fields = {}, None
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            name = safe_name(member.name)
            if member.isdir():
                continue
            require(member.isfile() and name not in hashes, "duplicate or non-file Debian control entry: " + name)
            contents = archive.extractfile(member).read()
            hashes[name] = hashlib.sha256(contents).hexdigest()
            if name == "control":
                fields = control_fields(contents.decode("utf-8"))
    require(fields and all(fields.get(field) for field in ("Package", "Version", "Architecture")),
            "Debian control must identify Package, Version and Architecture")
    return fields, hashes


def payload_extent(path):
    """Validate members and return the raw TAR prefix before its end markers."""
    count = 0
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            name = safe_name(member.name)
            require(not PurePosixPath(name).name.startswith(".wh."), "reserved OCI whiteout path: " + name)
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(),
                    "unsupported package payload member: " + name)
            require(member.sparse is None, "sparse package member is unsupported: " + name)
            if member.islnk():
                safe_name(member.linkname)
            count += 1
        end = archive.offset
    require(count, "empty package payload")
    with path.open("rb") as stream:
        stream.seek(end)
        require(stream.read(1024) == b"\0" * 1024, "missing TAR end markers")
        while tail := stream.read(1024 * 1024):
            require(not tail.strip(b"\0"), "unexpected data after TAR end markers")
    return count, end


def import_packages(mapping, payload, manifest_path, runtime_manifest=None):
    architecture = mapping["architecture"]
    require(isinstance(architecture, str) and architecture, "expected Debian architecture")
    metadata = mapping.get("metadata", {})
    reserved = {"schema", "architecture", "packages", "payload", "runtime_manifest_sha256"}
    require(isinstance(metadata, dict) and not reserved.intersection(metadata), "metadata overrides verified package facts")
    records, names, total_members = [], set(), 0
    payload.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deb-import-", dir=payload.parent) as temporary, payload.open("wb") as output:
        for index, item in enumerate(mapping["packages"]):
            source, control = Path(item["path"]), Path(item["control"])
            source_sha = sha(source)
            package_dir = Path(temporary) / str(index)
            package_dir.mkdir()
            compressed_control, compressed_data = unpack_ar(source, package_dir)
            control.parent.mkdir(parents=True, exist_ok=True)
            decompress(compressed_control, control)
            data = package_dir / "payload.tar"
            decompress(compressed_data, data)
            fields, control_files = inspect_control(control)
            name = fields["Package"]
            require(name not in names, "duplicate Debian package: " + name)
            names.add(name)
            require(fields["Architecture"] in (architecture, "all"), "foreign Debian architecture: " + name)
            count, end = payload_extent(data)
            with data.open("rb") as incoming:
                copy_bytes(incoming, output, end)
            total_members += count
            require(sha(source) == source_sha, "Debian input changed during import: " + source.name)
            records.append({
                "package": name, "version": fields["Version"], "architecture": fields["Architecture"],
                "source_deb": source.name, "source_label": item["label"],
                "source_sha256": source_sha, "source_size": source.stat().st_size,
                "control_sha256": sha(control), "control_fields": fields, "control_files": control_files,
                "payload_sha256": sha(data), "payload_size": data.stat().st_size, "payload_members": count,
            })
            shutil.rmtree(package_dir)
        require(records, "no Debian packages supplied")
        output.write(b"\0" * (1024 + (-(output.tell() + 1024) % 10240)))
    require(set(metadata.get("required_packages", [])).issubset(names), "missing required Debian package")
    result = {**metadata, "schema": 1, "architecture": architecture, "packages": records,
              "payload": {"path": payload.name, "sha256": sha(payload), "size": payload.stat().st_size,
                          "members": total_members}}
    if runtime_manifest is not None:
        runtime_bytes = runtime_manifest.read_bytes()
        runtime = json.loads(runtime_bytes)
        require(runtime.get("schema") == 1 and runtime.get("architecture") == architecture,
                "incompatible runtime package manifest")
        previous = {record["package"]: record for record in runtime["packages"]}
        for record in records:
            require(record["package"] not in previous or
                    previous[record["package"]]["source_sha256"] == record["source_sha256"],
                    "import replaces a runtime package: " + record["package"])
        result["runtime_manifest_sha256"] = hashlib.sha256(runtime_bytes).hexdigest()
    manifest_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--runtime-manifest", type=Path)
    args = parser.parse_args()
    import_packages(json.loads(args.inputs.read_text()), args.payload, args.manifest, args.runtime_manifest)


if __name__ == "__main__":
    main()
