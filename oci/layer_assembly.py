"""Assemble filtered SONiC layer archives without intermediate payload files."""

import argparse
from contextlib import ExitStack
from fnmatch import translate
from pathlib import Path, PurePosixPath
import re
import tarfile


def assemble(paths, output, *, includes=(), excludes=(), root_owned=False):
    # The include pass in the legacy filter came after the exclude pass, so
    # explicitly included members take precedence for the same installed path.
    include = re.compile("|".join(translate(pattern) for pattern in sorted(includes)) or "(?!)")
    exclude = re.compile("|".join(translate(pattern) for pattern in sorted(excludes)) or "(?!)")
    with ExitStack() as stack:
        members = {}
        for path in paths:
            archive = stack.enter_context(tarfile.open(path, "r:*"))
            for member in archive:
                name = str(PurePosixPath(member.name))
                # bsdtar renders directory names with a trailing slash.
                # Check both forms because exact patterns also match the directory.
                patterns = (name, name + "/") if member.isdir() else (name,)
                included = any(include.fullmatch(candidate) for candidate in patterns)
                if not included and any(exclude.fullmatch(candidate) for candidate in patterns):
                    continue
                if name in members and members[name][0] and not included:
                    continue
                members.pop(name, None)
                members[name] = (included, archive, member)
        with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as destination:
            for included, archive, member in sorted(members.values(), key=lambda item: item[0]):
                if root_owned:
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    member.pax_headers = {key: value for key, value in member.pax_headers.items()
                                          if key not in {"uid", "gid", "uname", "gname"}}
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        destination.addfile(member, stream)
                else:
                    destination.addfile(member)


def read_patterns(excludes, includes):
    return {
        "excludes": {str(PurePosixPath(line)) for path in excludes
                     for line in path.read_text().splitlines() if line},
        "includes": {str(PurePosixPath(pattern)) for pattern in includes},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--exclude", action="append", default=[], type=Path)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--root-owned", action="store_true")
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    paths = [archive for path in args.inputs
             for archive in (sorted(path.iterdir()) if path.is_dir() else [path])]
    assemble(paths, args.output, root_owned=args.root_owned, **read_patterns(args.exclude, args.include))


if __name__ == "__main__":
    main()
