"""Verify that the worker target exposes exactly its manifest's original DEBs."""

import hashlib
import json
from pathlib import Path
import sys


manifest_bytes = Path(sys.argv[1]).read_bytes()
manifest = json.loads(manifest_bytes)
assert set(manifest) == {"schema", "dependency_set", "architecture", "root_packages", "packages"}
assert manifest["schema"] == 1
assert manifest["dependency_set"] == "build_tools"
assert manifest["architecture"] == "amd64"
expected = {package["sha256"] for package in manifest["packages"].values()}
assert len(expected) == len(manifest["packages"]), "manifest contains duplicate archive hashes"

paths = [Path(value) for value in sys.argv[2:]]
assert len(paths) == len(set(paths)) == len(expected), "DEB target does not contain one file per manifest row"
actual = set()
for path in paths:
    assert path.name == "package.deb", path
    with path.open("rb") as stream:
        assert stream.read(8) == b"!<arch>\n", path
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert digest not in actual, "DEB target contains duplicate archive bytes"
    actual.add(digest)
assert actual == expected, "DEB target archives differ from the locked manifest"
print(json.dumps({"architecture": "amd64", "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                  "original_debs": len(actual), "result": "passed"}, sort_keys=True))
