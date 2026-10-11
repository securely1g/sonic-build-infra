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
assert subprocess.check_output([root / "bin/probe-static-math"], text=True) == "declared GCC link succeeded\n"
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
triplet = {"x86_64": "x86_64-linux-gnu", "aarch64": "aarch64-linux-gnu"}[platform.machine()]
sysroots = set()
for library in ["libgcc_s.so", "libgcc_s.so.1", "libc.so.6", "libc_nonshared.a", "libm.so.6", "libmvec.so.1"]:
    paths = [p for p in inputs if p.endswith("/" + library)]
    assert paths, library
    for path in paths:
        match = re.match(r"(.*/external/([^/]+)/root)/(.*)$", path)
        assert match, path
        assert match[2].endswith("+sonic_sysroot"), path
        # Linker scripts may use /lib through the sysroot's merged-/usr alias.
        assert match[3].startswith(("usr/lib/" + triplet + "/", "lib/" + triplet + "/", "usr/lib/gcc/" + triplet + "/")), path
        sysroots.add(match[1])
    print(library, paths[0])
assert len(sysroots) == 1, sysroots
assert not any(re.search(r"/external/[^/]*\+apt\+[^/]+/", p) for p in inputs), inputs
assert not any(p.startswith(("/usr/", "/lib/", "/lib64/")) for p in inputs), inputs
# GNU ld's dependency file records files it actually opened, including inputs
# named by libc/libm linker scripts. Reject host libraries in this receipt too.
dependencies = (root / "share/probe.d").read_text()
assert not re.search(r"(?:^|\s)/(?:usr/|lib/|lib64/)", dependencies), dependencies
for library in ["libc.so.6", "libc_nonshared.a", "libm.so.6", "libmvec.so.1"]:
    assert any(path in dependencies for path in inputs if path.endswith("/" + library)), library
print("shared sysroot:", next(iter(sysroots)))
static_map = (root / "share/probe-static-math.map").read_text()
static_inputs = re.findall(r"^LOAD (.+)$", static_map, re.MULTILINE)
static_dependencies = (root / "share/probe-static-math.d").read_text()
assert not any(p.startswith(("/usr/", "/lib/", "/lib64/")) for p in static_inputs), static_inputs
assert not re.search(r"(?:^|\s)/(?:usr/|lib/|lib64/)", static_dependencies), static_dependencies
for library in ["libm-2.41.a", "libmvec.a"]:
    paths = [p for p in static_inputs if p.endswith("/" + library)]
    assert paths and all(p.startswith(next(iter(sysroots)) + "/usr/lib/" + triplet + "/") for p in paths), paths
    assert all(p in static_dependencies for p in paths), library
    print("static math:", paths[0])
