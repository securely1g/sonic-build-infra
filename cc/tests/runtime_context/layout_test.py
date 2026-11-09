"""Resolve every indirect SONAME without adding it to the consumer's NEEDED list.

The legacy and paired-argument fixtures first require a successful sandboxed
native link. Inspecting the consumer and dependency ELF files then prevents a
fix that links all indirect libraries directly into the executable.
"""
import argparse
from pathlib import Path
from elf_metadata import Elf

parser = argparse.ArgumentParser()
parser.add_argument("--needed", action="append", default=[])
parser.add_argument("consumer")
parser.add_argument("dependency")
parser.add_argument("middle_soname")
parser.add_argument("indirect_sonames", nargs="+")
args = parser.parse_args()

consumer = Elf(Path(args.consumer).read_bytes())
dependency = Elf(Path(args.dependency).read_bytes())
for soname in [args.middle_soname, *args.needed]:
    assert soname in consumer.needed, (soname, consumer.needed)
for soname in args.indirect_sonames:
    assert soname not in consumer.needed, (soname, consumer.needed)
    assert soname in dependency.needed, (soname, dependency.needed)
