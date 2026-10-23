"""Bookworm original Valgrind version and real relocated instrumentation contract."""
import os
from pathlib import Path
import runpy
import subprocess
import sys

result = subprocess.run([str(Path(sys.argv[3]).absolute()), "--version"], env={"PATH": "", "LANG": "C", "LC_ALL": "C"}, capture_output=True, text=True)
assert result.returncode == 0 and result.stdout.strip() == "valgrind-3.19.0", (result.returncode, result.stdout, result.stderr)
print("PUBLIC_BOOKWORM_VALGRIND_3_19_PASS", flush=True)
runpy.run_path(str(Path(__file__).with_name("test_runtime_test.py")), run_name="__main__")
