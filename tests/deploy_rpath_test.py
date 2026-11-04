"""A deployment tar retains private-library search paths without Bazel paths."""
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

with tarfile.open(sys.argv[2]) as archive:
    members = {member.name.removeprefix("./"): member for member in archive}
    data = archive.extractfile(members["usr/bin/hello"]).read()
with tempfile.TemporaryDirectory() as directory:
    binary = Path(directory) / "hello"
    binary.write_bytes(data)
    dynamic = subprocess.check_output([sys.argv[1], "-d", str(binary)], text=True)
    assert re.findall(r"\((RPATH|RUNPATH)\).*?\[(.*?)\]", dynamic) == [
        ("RUNPATH", "$ORIGIN/../lib:/opt/sonic/lib")
    ], dynamic
