"""Normalize tar ownership and optionally regular-file modes, preserving payloads."""

import argparse
from pathlib import PurePosixPath
import tarfile


def root_owned_tar(source: str, destination: str, symlinks: dict[str, str] | None = None, *, regular_file_mode: int | None = None) -> None:
    with tarfile.open(source, "r|*") as archive:
        with tarfile.open(destination, "w|", format=tarfile.PAX_FORMAT) as output:
            seen = set()
            for member in archive:
                seen.add(str(PurePosixPath(member.name)))
                member.uid = member.gid = 0
                member.uname = member.gname = ""
                # PAX fields otherwise override the numeric and name headers.
                member.pax_headers = {
                    key: value
                    for key, value in member.pax_headers.items()
                    if key not in {"uid", "gid", "uname", "gname"}
                }
                if member.isfile():
                    if regular_file_mode is not None:
                        member.mode = regular_file_mode
                    with archive.extractfile(member) as contents:
                        output.addfile(member, contents)
                else:
                    output.addfile(member)

            for name, target in sorted((symlinks or {}).items()):
                path = PurePosixPath(name.removeprefix("./"))
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("invalid installation symlink path: " + name)
                if str(path) in seen:
                    raise ValueError("installation symlink duplicates archive entry: " + name)
                entry = tarfile.TarInfo("./" + str(path))
                entry.type, entry.mode, entry.linkname = tarfile.SYMTYPE, 0o777, target
                output.addfile(entry)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--symlink", nargs=2, action="append", default=[])
    parser.add_argument("--regular-file-mode", type=lambda value: int(value, 8))
    args = parser.parse_args()
    root_owned_tar(args.source, args.destination, dict(args.symlink), regular_file_mode=args.regular_file_mode)
