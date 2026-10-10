"""Extract declared Debian inputs without executing package installation code.

The declared tar.bzl BSD tar toolchain extracts package members and composes
PAX archives. Python only validates archives and records package facts; it never
writes TAR headers, decompresses an archive into an output, or installs files.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_tar(bsdtar, arguments, output=None):
    """Invoke the declared upstream tool; never fall back to host commands."""
    command = [str(bsdtar), *map(str, arguments)]
    try:
        if output is None:
            return subprocess.run(command, check=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE).stdout
        with Path(output).open("wb") as stream:
            subprocess.run(command, check=True, stdout=stream, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        raise ValueError("BSD tar rejected archive: " + error.stderr.decode("utf-8", "replace")) from error


def extract_archives(source, destination, bsdtar):
    """Select Debian members using BSD tar, without parsing the ar format."""
    with source.open("rb") as stream:
        require(stream.read(8) == b"!<arch>\n", "not a Debian ar archive: " + source.name)
    members = run_tar(bsdtar, ["-tf", source]).decode("ascii").splitlines()
    suffixes = ("", ".gz", ".xz", ".bz2")
    require(len(members) == 3 and members[0] == "debian-binary" and
            members[1] in {"control.tar" + suffix for suffix in suffixes} and
            members[2] in {"data.tar" + suffix for suffix in suffixes},
            "expected debian-binary, control.tar and data.tar with supported compression, in that order")
    require(run_tar(bsdtar, ["-xOf", source, "debian-binary"]) == b"2.0\n",
            "unsupported Debian archive version")
    paths = [destination / member for member in members[1:]]
    for member, path in zip(members[1:], paths):
        run_tar(bsdtar, ["-xOf", source, member], path)
    return paths


def rewrite_archive(source, destination, bsdtar):
    """Use upstream archive passthrough; never extract payloads onto the host."""
    run_tar(bsdtar, ["--format", "pax", "-cf", destination, "@" + str(source)])


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


def archive_facts(path, payload=False):
    """Read original archive bytes and semantic metadata without producing an archive.

    tarfile supplies the existing read-only stream for supported compression.
    Rewinding that stream hashes the entire original decompressed TAR, including
    padding, so legacy package pins do not change when BSD tar normalizes headers.
    """
    members = []
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            name = safe_name(member.name)
            if payload:
                require(not PurePosixPath(name).name.startswith(".wh."), "reserved OCI whiteout path: " + name)
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(),
                    "unsupported package archive member: " + name)
            require(member.sparse is None, "sparse package member is unsupported: " + name)
            if member.islnk():
                safe_name(member.linkname)
            digest = None
            if member.isfile():
                with archive.extractfile(member) as contents:
                    digest = hashlib.file_digest(contents, "sha256").hexdigest()
            members.append({
                "name": member.name, "mode": member.mode, "uid": member.uid, "gid": member.gid,
                "uname": member.uname, "gname": member.gname, "mtime": member.mtime,
                "size": member.size, "type": "file" if member.isfile() else member.type.decode("ascii"),
                "linkname": member.linkname, "sha256": digest,
                # These standardized PAX values are already checked above as
                # parsed fields. Keep all other extended metadata unchanged.
                "pax": {key: value for key, value in member.pax_headers.items()
                        if key not in ("path", "linkpath", "size", "uid", "gid", "uname", "gname", "mtime")},
            })
        require(members, "empty package archive")
        end = archive.offset
        stream = archive.fileobj
        stream.seek(end)
        require(stream.read(1024) == b"\0" * 1024, "missing TAR end markers")
        while tail := stream.read(1024 * 1024):
            require(not tail.strip(b"\0"), "unexpected data after TAR end markers")
        size = stream.tell()
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"members": members, "end": end, "size": size, "sha256": digest}


def payload_extent(path):
    """Validate a payload archive and return its member count and TAR end offset."""
    facts = archive_facts(path, payload=True)
    return len(facts["members"]), facts["end"]


def verify_members(expected, actual):
    """Fail if upstream normalization loses contents, ordering or required metadata."""
    require(len(expected) == len(actual), "archive rewrite changed the member count")
    for original, rewritten in zip(expected, actual):
        require({key: value for key, value in original.items() if key != "pax"} ==
                {key: value for key, value in rewritten.items() if key != "pax"},
                "archive rewrite changed member metadata: " + original["name"])
        require(all(rewritten["pax"].get(key) == value for key, value in original["pax"].items()),
                "archive rewrite lost extended metadata: " + original["name"])


def import_packages(mapping, payload, manifest_path, runtime_manifest=None, *, bsdtar):
    architecture = mapping["architecture"]
    require(isinstance(architecture, str) and architecture, "expected Debian architecture")
    metadata = mapping.get("metadata", {})
    reserved = {"schema", "architecture", "packages", "payload", "runtime_manifest_sha256"}
    require(isinstance(metadata, dict) and not reserved.intersection(metadata), "metadata overrides verified package facts")
    records, names, expected_members = [], set(), []
    payload.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deb-import-", dir=payload.parent) as temporary:
        data_archives = []
        for index, item in enumerate(mapping["packages"]):
            source, control = Path(item["path"]), Path(item["control"])
            source_sha = sha(source)
            package_dir = Path(temporary) / str(index)
            package_dir.mkdir()
            source_control, source_data = extract_archives(source, package_dir, bsdtar)
            control_facts = archive_facts(source_control)
            data_facts = archive_facts(source_data, payload=True)
            control.parent.mkdir(parents=True, exist_ok=True)
            rewrite_archive(source_control, control, bsdtar)
            verify_members(control_facts["members"], archive_facts(control)["members"])
            fields, control_files = inspect_control(control)
            name = fields["Package"]
            require(name not in names, "duplicate Debian package: " + name)
            names.add(name)
            require(fields["Architecture"] in (architecture, "all"), "foreign Debian architecture: " + name)
            data_archives.append(source_data)
            expected_members.extend(data_facts["members"])
            require(sha(source) == source_sha, "Debian input changed during import: " + source.name)
            records.append({
                "package": name, "version": fields["Version"], "architecture": fields["Architecture"],
                "source_deb": source.name, "source_label": item["label"],
                "source_sha256": source_sha, "source_size": source.stat().st_size,
                "control_sha256": control_facts["sha256"], "control_output_sha256": sha(control),
                "control_fields": fields, "control_files": control_files,
                "payload_sha256": data_facts["sha256"], "payload_size": data_facts["size"],
                "payload_members": len(data_facts["members"]),
            })
        require(records, "no Debian packages supplied")
        require(set(metadata.get("required_packages", [])).issubset(names), "missing required Debian package")
        run_tar(bsdtar, ["--format", "pax", "-cf", payload, *["@" + str(path) for path in data_archives]])
        verify_members(expected_members, archive_facts(payload, payload=True)["members"])
    result = {**metadata, "schema": 1, "architecture": architecture, "packages": records,
              "payload": {"path": payload.name, "sha256": sha(payload), "size": payload.stat().st_size,
                          "members": len(expected_members)}}
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
    parser.add_argument("--bsdtar", required=True, type=Path)
    args = parser.parse_args()
    import_packages(json.loads(args.inputs.read_text()), args.payload, args.manifest, args.runtime_manifest, bsdtar=args.bsdtar)


if __name__ == "__main__":
    main()
