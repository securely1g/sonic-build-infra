"""The target compiler must preserve configure caller paths and outputs."""
import json
from pathlib import Path
import sys
p = Path(sys.argv[1]) / "configuration"
report = json.loads((p / "result.json").read_text())
assert not report["source_default_object_exists"]
assert not report["source_default_assembly_exists"]
for name in ["a.out", "outputs with spaces/explicit program", "input source.o", "outputs with spaces/explicit object.o"]:
    assert (p / name).read_bytes().startswith(b"\x7fELF"), name
assert "main" in (p / "input source.s").read_text()
assert "probe" in (p / "outputs with spaces/dependency file.d").read_text()
assert (p / "library output/libprobe.a").read_bytes().startswith(b"!<arch>\n")
print("configure compiler paths, large argv, actual compilation/linking and default outputs: PASS")

linked = (p / "rpath caller/linked program").read_bytes()
assert linked.startswith(b"\x7fELF")
assert b"$ORIGIN/../link first:$ORIGIN/../link second with spaces" in linked
