"""Forcing debug retention and consuming retained input preserve the same ELF."""
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def readelf(tool, path, flag):
    return subprocess.check_output([tool, flag, str(path)], text=True, errors="replace")


def main():
    tool = sys.argv[1]
    true_runtime, true_debug, retained_runtime, retained_debug = map(Path, sys.argv[2:])
    assert true_runtime.read_bytes() == retained_runtime.read_bytes()
    assert true_debug.read_bytes() == retained_debug.read_bytes()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for archive in (retained_runtime, retained_debug):
            with tarfile.open(archive) as package:
                package.extractall(root, filter="data")
        runtime = root / "usr/bin/probe"
        symbols, = root.glob("usr/lib/debug/.build-id/*/*.debug")
        assert ".debug_info" not in readelf(tool, runtime, "-SW")
        assert ".debug_info" in readelf(tool, symbols, "-SW")
        identity = lambda path: re.search(r"Build ID: ([0-9a-f]+)", readelf(tool, path, "-n")).group(1)
        assert identity(runtime) == identity(symbols)
        assert symbols.name in readelf(tool, runtime, "-p.gnu_debuglink")
    print("Explicit True and already-retained input preserve identical runtime/debug TARs")


if __name__ == "__main__":
    main()
