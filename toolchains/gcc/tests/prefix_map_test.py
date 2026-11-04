"""Validate dpkg-buildflags file-prefix-map semantics in real GCC outputs."""
from pathlib import Path
import re
import sys

obj = next(Path(name) for name in sys.argv[1:] if name.endswith('.o'))
dwarf = next(Path(name) for name in sys.argv[1:] if name.endswith('.dwarf')).read_text()
# Independent requirement: dpkg's -ffile-prefix-map=<compile cwd>=. maps
# DW_AT_comp_dir and the __FILE__ expansion, including absolute source inputs.
assert re.search(r'DW_AT_comp_dir\s*:.*:\s*\.\s*$', dwarf, re.MULTILINE) or re.search(r'DW_AT_comp_dir\s*:\s*\.\s*$', dwarf, re.MULTILINE), dwarf
assert b'./toolchains/gcc/tests/prefix_map.c\x00' in obj.read_bytes()
