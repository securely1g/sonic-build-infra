"""Build metadata for one real public package input; no DEBs are produced."""

import hashlib
import json
from pathlib import Path
import sys
import tarfile

payload, control, output = map(Path, sys.argv[1:])
with tarfile.open(control) as archive:
    text = archive.extractfile("./control" if "./control" in archive.getnames() else "control").read().decode()
fields = dict(line.split(": ", 1) for line in text.splitlines() if line and not line[0].isspace() and ": " in line)
assert fields["Package"] == "aspell-en" and fields["Version"] == "2020.12.07-0-1" and fields["Architecture"] == "all"
key = "/test/aspell-en:all=2020.12.07-0-1"
package = {"name": "aspell-en", "version": fields["Version"], "architecture": "all", "sha256": "0" * 64, "depends_on": []}
for kind, path in (("payload", payload), ("control", control)):
    package[kind + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    package[kind + "_size"] = path.stat().st_size
lock = {"version": 2, "dependency_sets": {"dictionary": {"sets": {arch: {key.rsplit("=", 1)[0]: fields["Version"]} for arch in ("amd64", "arm64")}}}, "packages": {key: package}}
output.write_text(json.dumps(lock))
