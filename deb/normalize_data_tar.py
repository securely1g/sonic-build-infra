#!/usr/bin/env python3
"""Emit a deterministic dpkg data tar with parents before their children.

Build-layer tars may omit parents or list them after descendants. Unlike general
purpose tar extraction, dpkg requires the parent entries before unpacking each
member. Index headers, then stream payloads to a new tar without extracting them.
"""

from __future__ import annotations

import copy
from pathlib import PurePosixPath
import sys
import tarfile


def archive_path(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Package member must use a relative path: " + name)
    return str(path)


def directory(name: str) -> tarfile.TarInfo:
    result = tarfile.TarInfo("." if name == "." else "./" + name)
    result.type = tarfile.DIRTYPE
    result.mode = 0o755
    result.uid = result.gid = result.mtime = 0
    return result


def normalize(data_tar: str, output_tar: str) -> None:
    with tarfile.open(data_tar, "r:*") as source:
        members = {}
        for member in source.getmembers():
            name = archive_path(member.name)
            if name in members:
                raise ValueError("Duplicate package member: " + name)
            members[name] = member
        for name in list(members):
            for parent in PurePosixPath(name).parents:
                key = str(parent)
                if key not in members:
                    members[key] = directory(key)
                elif not (members[key].isdir() or members[key].issym()):
                    raise ValueError("Package parent is not a directory or symlink: " + key)
        if "." not in members:
            members["."] = directory(".")
        if not members["."].isdir():
            raise ValueError("Package root must be a directory")

        written = set()
        pending = set()
        with tarfile.open(output_tar, "w", format=tarfile.PAX_FORMAT) as output:
            def emit(name: str) -> None:
                if name in written:
                    return
                if name in pending:
                    raise ValueError("Cyclic package links/parents: " + name)
                if name not in members:
                    raise ValueError("Package hardlink target is missing: " + name)
                pending.add(name)
                member = members[name]
                if name != ".":
                    emit(str(PurePosixPath(name).parent))
                if member.islnk():
                    emit(archive_path(member.linkname))
                payload = source.extractfile(member) if member.isfile() else None
                # tarfile reconstructs sparse-file bytes when reading. Emit a
                # regular dense member rather than copying stale sparse maps.
                header = copy.copy(member)
                header.pax_headers = {key: value for key, value in member.pax_headers.items()
                                      if not key.startswith("GNU.sparse.")}
                if member.sparse is not None:
                    header.type = tarfile.REGTYPE
                header.sparse = None
                try:
                    output.addfile(header, payload)
                finally:
                    if payload is not None:
                        payload.close()
                pending.remove(name)
                written.add(name)

            for name in sorted(members):
                emit(name)


if __name__ == "__main__":
    normalize(sys.argv[1], sys.argv[2])
