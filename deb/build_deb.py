#!/usr/bin/env python3
"""Stage a deployment tar and build a Debian package with pinned dpkg-deb."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile


def package_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Package member must use a relative path: " + name)
    if path.parts and path.parts[0] == "DEBIAN":
        raise ValueError("DEBIAN is reserved for package control files: " + name)
    return path


def payload_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Validate staging paths and visit hardlink targets before their links."""
    members = {}
    for member in archive:
        path = package_path(member.name)
        if path in members:
            raise ValueError("Duplicate package member: " + member.name)
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise ValueError("Unsupported package member type: " + member.name)
        if path == PurePosixPath(".") and not member.isdir():
            raise ValueError("Package root must be a directory")
        members[path] = member
    for path in members:
        for parent in path.parents:
            if parent in members and not members[parent].isdir():
                raise ValueError("Package parent must be a directory: " + str(parent))

    ordered = []
    visited = set()
    pending = set()

    def visit(path: PurePosixPath) -> None:
        if path in visited:
            return
        if path in pending:
            raise ValueError("Cyclic package hardlink: " + str(path))
        if path not in members:
            raise ValueError("Missing package hardlink target: " + str(path))
        pending.add(path)
        member = members[path]
        if member.islnk():
            target = package_path(member.linkname)
            visit(target)
            if not (members[target].isfile() or members[target].islnk()):
                raise ValueError("Package hardlink target must be a regular file: " + str(target))
        ordered.append(member)
        visited.add(path)
        pending.remove(path)

    for path in members:
        visit(path)
    return ordered


def stage_payload(data_tar: Path, package_root: Path) -> None:
    with tarfile.open(data_tar, "r:*") as archive:
        members = payload_members(archive)
        # The path checks above forbid traversal and symlink parents, while
        # allowing legitimate absolute symlinks as leaf entries. Leave modes
        # intact and suppress chown: dpkg-deb writes root ownership later.
        def without_owner(member: tarfile.TarInfo, _destination: str) -> tarfile.TarInfo:
            return member.replace(uid=None, gid=None, uname=None, gname=None, deep=False)

        previous_umask = os.umask(0o022)
        try:
            # Standard extraction creates omitted parents, restores directory
            # metadata after descendants, and expands sparse file contents.
            archive.extractall(package_root, members=members, filter=without_owner)
        finally:
            os.umask(previous_umask)


def write_md5sums(package_root: Path, output: Path) -> None:
    files = []
    for directory, directories, names in os.walk(package_root, followlinks=False):
        if Path(directory) == package_root:
            directories.remove("DEBIAN")
        for name in names:
            path = Path(directory) / name
            if stat.S_ISREG(path.lstat().st_mode):
                files.append(path.relative_to(package_root))
    with output.open("w", encoding="utf-8") as sums:
        for relative in sorted(files):
            with (package_root / relative).open("rb") as payload:
                digest = hashlib.file_digest(payload, "md5").hexdigest()
            sums.write("%s  %s\n" % (digest, relative.as_posix()))
    output.chmod(0o644)


def build_deb(data_tar: str, control: str, output: str, dpkg_deb: str) -> None:
    # Resolve action inputs before running the extractor in the staging tree.
    source = Path(data_tar).resolve()
    control_file = Path(control).resolve()
    output_file = Path(output).resolve()
    # Keep launcher symlinks intact: their adjacent runfiles directories belong
    # to the action and may differ from those beside the underlying files.
    dpkg_tool = Path(dpkg_deb).absolute()
    with tempfile.TemporaryDirectory(prefix="sonic-deb-", dir=output_file.parent) as temporary:
        package_root = Path(temporary) / "package"
        package_root.mkdir()
        package_root.chmod(0o755)
        stage_payload(source, package_root)
        control_directory = package_root / "DEBIAN"
        control_directory.mkdir()
        control_directory.chmod(0o755)
        shutil.copyfile(control_file, control_directory / "control")
        (control_directory / "control").chmod(0o644)
        write_md5sums(package_root, control_directory / "md5sums")
        # A nested Bazel Python launcher must discover its own runfiles instead
        # of inheriting this runner's tree and Python import path.
        environment = {key: value for key, value in os.environ.items() if key not in (
            "RUNFILES_DIR", "RUNFILES_MANIFEST_FILE", "JAVA_RUNFILES", "PYTHONPATH",
        )}
        environment.update(SOURCE_DATE_EPOCH="0", LC_ALL="C", TZ="UTC")
        subprocess.run([
            str(dpkg_tool), "--root-owner-group", "-Zgzip",
            "--uniform-compression", "--threads-max=1", "--build",
            str(package_root), str(output_file),
        ], env=environment, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-tar", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dpkg-deb", required=True)
    build_deb(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
