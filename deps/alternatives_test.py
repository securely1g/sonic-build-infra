"""Check the TARs consumed by Syncd's P4 compiler and MPI installation."""

import posixpath
import sys
import tarfile

entries = {}
for path in sys.argv[1:]:
    with tarfile.open(path) as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            assert name not in entries, ("duplicate path", name)
            assert member.issym(), ("alternatives must only create links", name)
            assert (member.uid, member.gid, member.mode, member.mtime) == (0, 0, 0o777, 0), name
            assert member.linkname.startswith("/"), name
            entries[name] = member.linkname.lstrip("/")


def destination(path):
    visited = set()
    while path in entries:
        assert path not in visited, ("link cycle", path)
        visited.add(path)
        path = posixpath.normpath(entries[path])
    return path


# The P4 compiler invokes the generic compiler names. Debian's MPI wrappers
# must resolve to OpenMPI, including the C++ spelling and multiarch includes.
assert destination("usr/bin/cc") == "usr/bin/gcc"
assert destination("usr/bin/c++") == "usr/bin/clang++"
assert destination("usr/lib/cpp") == "usr/bin/cpp"
assert destination("usr/bin/aclocal") == "usr/bin/aclocal-1.17"
assert destination("usr/bin/automake") == "usr/bin/automake-1.17"
assert destination("usr/bin/mpicc") == "usr/bin/mpicc.openmpi"
assert destination("usr/bin/mpicxx") == "usr/bin/mpic++.openmpi"
triplets = [triplet for triplet in ("x86_64-linux-gnu", "aarch64-linux-gnu")
            if "usr/include/" + triplet + "/mpi" in entries]
assert len(triplets) == 1, triplets
triplet = triplets[0]
assert destination("usr/include/" + triplet + "/mpi") == "usr/lib/" + triplet + "/openmpi/include"
assert destination("usr/lib/" + triplet + "/libmpi.so") == "usr/lib/" + triplet + "/openmpi/lib/libmpi.so"
# Every installed generic name must have exactly one alternatives hop and a
# concrete destination, with no orphaned /etc/alternatives entry.
public = {path for path in entries if not path.startswith("etc/alternatives/")}
assert len(public) == 21
assert set(entries) - public == {entries[path] for path in public}
assert all(not destination(path).startswith("etc/alternatives/") for path in public)
print("Validated 21 compiler, Automake and MPI alternatives on " + triplet)
