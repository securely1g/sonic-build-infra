"""Assemble the declared Debian execution tools for offline kernel builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile

from prepare_runtime import extract_payload, loader_path


def control_fields(text: str) -> dict[str, str]:
    fields = {}
    current = None
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and current:
            fields[current] += "\n" + line
        elif ":" in line:
            current, value = line.split(":", 1)
            fields[current] = value.strip()
    return fields


def package_metadata(root: Path, data: Path, control: Path) -> None:
    """Install dpkg's library ownership and dependency metadata, not maintscripts."""
    records = {}
    with tarfile.open(control, "r:*") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if name in ("control", "shlibs", "symbols"):
                if not member.isfile():
                    raise ValueError(f"invalid package control entry: {name}")
                with archive.extractfile(member) as source:
                    records[name] = source.read()
    fields = control_fields(records["control"].decode())
    name = fields["Package"]
    architecture = fields["Architecture"]
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789+.-" for c in name):
        raise ValueError("invalid Debian package name")
    if architecture not in ("amd64", "arm64", "all"):
        raise ValueError("unsupported package metadata architecture")
    qualified = name + (":" + architecture if fields.get("Multi-Arch") == "same" else "")
    info = root / "var/lib/dpkg/info"
    info.mkdir(parents=True, exist_ok=True)
    # Format 1 enables the architecture-qualified metadata filenames used by
    # Multi-Arch packages; without it dpkg silently searches unqualified names.
    (info / "format").write_text("1\n")
    paths = []
    with tarfile.open(data, "r:*") as archive:
        for member in archive:
            relative = PurePosixPath(member.name.removeprefix("./"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"invalid package file path: {member.name}")
            paths.append("/" + str(relative).rstrip("/"))
    (info / (qualified + ".list")).write_text("\n".join(sorted(set(paths))) + "\n")
    for kind in ("shlibs", "symbols"):
        if kind in records:
            (info / (qualified + "." + kind)).write_bytes(records[kind])


def initialize_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("bin", "sbin", "lib", "lib32", "lib64", "libx32"):
        (root / "usr" / name).mkdir(parents=True, exist_ok=True)
        (root / name).symlink_to("usr/" + name)
    for name in ("tmp", "dev", "proc", "etc", "var/lib/dpkg/info"):
        (root / name).mkdir(parents=True, exist_ok=True)


def install_alternatives(root: Path) -> None:
    # Unpacking Debian payloads does not execute postinst or alternatives.
    # Select one declared implementation for each build command we require.
    alternatives = {
        "usr/bin/sh": "usr/bin/dash",
        "usr/bin/awk": "usr/bin/mawk",
        "usr/bin/cc": "usr/bin/gcc",
        "usr/bin/c++": "usr/bin/g++",
        "usr/bin/lex": "usr/bin/flex",
        "usr/bin/yacc": "usr/bin/bison.yacc",
        "usr/lib/cpp": "usr/bin/cpp",
        "usr/sbin/rmt": "usr/sbin/rmt-tar",
        "etc/localtime": "usr/share/zoneinfo/Etc/UTC",
        "etc/dpkg/origins/default": "etc/dpkg/origins/debian",
    }
    for command in ("automake", "aclocal"):
        versions = sorted((root / "usr/bin").glob(command + "-[0-9]*"))
        if len(versions) == 1:
            alternatives["usr/bin/" + command] = str(versions[0].relative_to(root))
    for destination, source in alternatives.items():
        source = root / source
        target = root / destination
        if not source.is_file():
            raise RuntimeError(f"missing declared alternative: {source.relative_to(root)}")
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(os.path.relpath(source, target.parent))


def validate_links(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            continue
        try:
            destination = path.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise ValueError(f"invalid runtime symlink: {path.relative_to(root)}") from error
        if not destination.is_relative_to(root):
            raise ValueError(f"runtime symlink escapes output: {path.relative_to(root)}")
        # These optional files are outside the offline compiler/packager
        # contract: system CAs, Quilt's mail helper, and documentation targets.
        optional = str(path.relative_to(root)) in (
            "usr/lib/ssl/cert.pem", "usr/share/quilt/compat/sendmail",
        ) or any(path.is_relative_to(root / part) for part in ("usr/share/man", "usr/share/doc"))
        if not destination.exists() and optional:
            path.unlink()
        elif not destination.exists():
            raise ValueError(f"unresolved runtime symlink: {path.relative_to(root)} -> {path.readlink()}")


def input_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preserve_empty_directories(root: Path) -> None:
    # Bazel 8 can omit empty directories when materializing a cached tree,
    # including usr/lib32 and usr/libx32 referenced by the merged-/usr links.
    # A zero-byte file keeps each empty leaf present through that transport.
    # Do not follow directory symlinks or add files to nonempty directories.
    for directory, _, _ in os.walk(root, followlinks=False):
        path = Path(directory)
        if not any(path.iterdir()):
            (path / ".bazel-keep-directory").touch()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--tar", type=Path, action="append", default=[])
    parser.add_argument("--metadata", type=Path, nargs=2, action="append", default=[])
    args = parser.parse_args()
    root = args.out.resolve()
    initialize_root(root)
    for payload in sorted(set(args.tar)):
        extract_payload(root, payload)
    for data, control in args.metadata:
        package_metadata(root, data, control)
    install_alternatives(root)
    loader_path(root, args.architecture)
    validate_links(root)
    status = root / "var/lib/dpkg/status"
    packages = {}
    for record in status.read_text().split("\n\n"):
        fields = control_fields(record)
        if fields.get("Package"):
            packages[fields["Package"]] = fields["Version"]
    # The identity is path-independent across standalone and consuming modules.
    # Bazel also hashes this declared tree as an input to the kernel action.
    inputs = set(args.tar) | {path for pair in args.metadata for path in pair}
    inputs |= {Path(__file__), Path(__file__).with_name("prepare_runtime.py")}
    digests = sorted(input_digest(path) for path in inputs)
    identity = hashlib.sha256(json.dumps({"architecture": args.architecture, "inputs": digests}, sort_keys=True).encode()).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "debian-build-tools",
        "architecture": args.architecture,
        "identity_sha256": identity,
        "input_sha256": digests,
        "packages": packages,
    }
    (root / "kernel-runtime.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    # Account lookup and temporary files work without inheriting host /etc.
    (root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/sh\n")
    (root / "etc/group").write_text("root:x:0:\n")
    preserve_empty_directories(root)
    (root / "tmp").chmod(0o1777)
    for path in root.rglob("*"):
        if not path.is_symlink():
            os.utime(path, (0, 0))


if __name__ == "__main__":
    main()
