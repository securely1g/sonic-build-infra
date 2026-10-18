"""Check the source-library ABI policy reaches the actual consumer control."""

import hashlib
import json
from pathlib import Path
import platform
import sys

binary, library, symbols, receipt = map(Path, sys.argv[1:])
architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
control = binary.read_text()
assert "Package: metadata-probe\n" in control, control
assert "Version: 2:1.2.3-4+sonic.1\n" in control, control
assert "Architecture: " + architecture + "\n" in control, control
assert "libmetadata-probe1 (>= 1.0-1)" in control, control
assert "libc6 (>= " in control, control
assert "${" not in control, control
assert int(next(line.split(": ")[1] for line in control.splitlines() if line.startswith("Installed-Size:"))) > 0
assert "Package: libmetadata-probe1\n" in library.read_text()
assert "Version: 1.0-1\n" in library.read_text()
assert "metadata_probe_value@Base 1.0-1" in symbols.read_text()
evidence = json.loads(receipt.read_text())
assert evidence["status"] == "passed"
assert evidence["architecture"] == architecture
assert evidence["elf_files"] == ["usr/bin/metadata-probe"]
assert evidence["outputs"]["control"] == hashlib.sha256(binary.read_bytes()).hexdigest()
print(control)
