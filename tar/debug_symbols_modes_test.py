"""Inspect the actual archive produced by the debug-file metadata mutation."""

import sys
import tarfile


with tarfile.open(sys.argv[1]) as archive:
    members = {member.name.removeprefix("./"): member for member in archive}
    assert set(members) == {
        "symbols", "symbols/file.debug", "symbols/symlink.debug",
    }, members
    directory = members["symbols"]
    assert directory.isdir() and directory.mode == 0o755 and directory.mtime == 111
    regular = members["symbols/file.debug"]
    assert regular.isfile() and regular.mode == 0o644 and regular.mtime == 222
    assert archive.extractfile(regular).read() == b"fixture symbols\n"
    symlink = members["symbols/symlink.debug"]
    assert symlink.issym() and symlink.mode == 0o777 and symlink.mtime == 333
    assert symlink.linkname == "file.debug"
    assert all(member.uid == 17 and member.gid == 23 for member in members.values())
