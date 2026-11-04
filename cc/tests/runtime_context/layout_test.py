"""The consumer resolves the indirect library without linking it directly."""
from pathlib import Path
import sys
from elf_metadata import Elf

consumer, middle = [Elf(Path(path).read_bytes()) for path in sys.argv[1:]]
assert "libnative_context_middle.so.0" in consumer.needed
assert "libindirect.so.7" not in consumer.needed
assert "libindirect.so.7" in middle.needed
