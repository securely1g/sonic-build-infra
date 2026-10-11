"""Run the binary configured and compiled through the declared toolchains."""

from pathlib import Path
import subprocess
import sys

binary = Path(sys.argv[1]).absolute()
assert subprocess.check_output([binary], text=True) == "declared tools: 42\n"
