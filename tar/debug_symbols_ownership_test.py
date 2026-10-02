"""Check actual debug_symbols_tar headers, including synthesized directories."""

import sys
import tarfile


with tarfile.open(sys.argv[1]) as archive:
    members = archive.getmembers()
    assert members, "debug-symbol tar is empty"
    assert any(member.isdir() for member in members), "missing synthesized directories"
    symbols = [member for member in members if member.isfile()]
    assert len(symbols) == 1, symbols
    assert symbols[0].name.endswith("/usr/lib/debug/.build-id/ab/fixture.debug"), symbols[0].name
    assert archive.extractfile(symbols[0]).read() == b"fixture symbols\n"
    for member in members:
        assert member.uid == member.gid == 0, (member.name, member.uid, member.gid)
        assert member.uname == member.gname == "", (member.name, member.uname, member.gname)
