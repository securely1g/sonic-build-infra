"""Run the foreign-built executable and inspect its actual linker inputs."""
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys

binary = next(Path(p).absolute() for p in sys.argv[1:] if p.endswith("/bin/probe"))
root = binary.parent.parent
assert subprocess.check_output([root / "bin/probe"], text=True) == "declared GCC link succeeded\n"
elf = binary.read_bytes()
assert elf[:6] == b"\x7fELF\x02\x01"
assert struct.unpack_from("<H", elf, 18)[0] == {"x86_64": 62, "aarch64": 183}[platform.machine()]
for name in ["assembler.txt", "linker.txt"]:
    value = (root / "share" / name).read_text().strip()
    print(name, value)
    assert value.startswith("/"), value
    assert "/external/" in value, value
    assert not value.startswith(("/usr/", "/lib/")), value
linker = (root / "share/linker.txt").read_text().strip()
execution = (root / "share/linker-execution.txt").read_text()
commands = [line.split()[0] for line in execution.splitlines() if line.startswith("/")]
assert linker in commands, (linker, commands, execution)
assert not any(command.startswith(("/usr/", "/lib/")) for command in commands), commands
print("actual linker:", linker)
map_text = (root / "share/probe.map").read_text()
inputs = re.findall(r"^LOAD (.+)$", map_text, re.MULTILINE)
for library, package in [("libgcc_s.so", "libgcc-14-dev"),
                         ("libgcc_s.so.1", "libgcc-s1"),
                         ("libc.so.6", "libc6")]:
    paths = [p for p in inputs if p.endswith("/" + library)]
    assert paths, library
    assert all("/external/" in p and package in p and "/usr/lib/" in p for p in paths), paths
    print(library, paths[0])
assert not any(p.startswith(("/usr/", "/lib/", "/lib64/")) for p in inputs), inputs
