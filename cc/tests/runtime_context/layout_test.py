"""Resolve every indirect SONAME without adding it to the consumer's NEEDED list.

The legacy and paired-argument fixtures first require a successful sandboxed
native link. Inspecting the consumer and dependency ELF files then prevents a
fix that links all indirect libraries directly into the executable.
"""
from pathlib import Path
import sys
from elf_metadata import Elf

consumer, dependency = [Elf(Path(path).read_bytes()) for path in sys.argv[1:3]]
middle_soname, *indirect_sonames = sys.argv[3:]
assert indirect_sonames
assert middle_soname in consumer.needed
for soname in indirect_sonames:
    assert soname not in consumer.needed, (soname, consumer.needed)
    assert soname in dependency.needed, (soname, dependency.needed)
