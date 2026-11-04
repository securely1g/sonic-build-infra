"""Target ABI contract independent of the executor's libc/toolchain."""
from pathlib import Path
import sys
from deb.elf_metadata import Elf
elf = Elf(Path(sys.argv[1]).read_bytes())
assert elf.machine == int(sys.argv[2]), (elf.machine, sys.argv[2])
assert elf.build_id
assert 'libc.so.6' in elf.needed
assert 'libstdc++.so.6' in elf.needed
versions = [version for library, version in elf.required_versions if library == 'libc.so.6']
assert versions, elf.required_versions
for version in versions:
    if version.startswith('GLIBC_') and version[6:][:1].isdigit():
        assert tuple(map(int, version[6:].split('.'))) <= (2, 36), version
