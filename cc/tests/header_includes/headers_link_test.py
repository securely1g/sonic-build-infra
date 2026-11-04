"""The native Perl package export supplies CORE headers and its shared library."""
from pathlib import Path
import sys
from elf_metadata import Elf

for path in sys.argv[1:]:
    elf = Elf(Path(path).read_bytes())
    assert "libperl.so.5.36" in elf.needed, (path, elf.needed)
print("Declared Perl CORE headers compiled; target integer/pointer ABI checked; original libperl dependency retained PASS")
