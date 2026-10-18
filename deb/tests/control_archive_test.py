"""Inspect the exact control members that the DEB assembler will consume."""

import hashlib
import sys
import tarfile


with tarfile.open(sys.argv[1]) as archive:
    members = {member.name.removeprefix("./"): member for member in archive if member.isfile()}
    assert set(members) == {"control", "md5sums", "conffiles", "postinst", "triggers"}, members
    for name, member in members.items():
        assert member.mode == (0o755 if name == "postinst" else 0o644), (name, member.mode)
        assert member.uid == member.gid == 0, member
    def contents(name):
        return archive.extractfile(members[name]).read().decode()
    assert "Version: 1:1.0-1\n" in contents("control")
    assert "Multi-Arch: same\n" in contents("control")
    assert "Depends: libc6 (>= 2.41)\n" in contents("control")
    assert contents("conffiles") == "/etc/example.conf\n"
    assert contents("triggers") == "activate-noawait ldconfig\n"
    assert contents("postinst").startswith("#!/bin/sh\n")
    expected = hashlib.md5(b"example = configured\n").hexdigest()
    assert contents("md5sums").strip() == expected + "  etc/example.conf", contents("md5sums")
