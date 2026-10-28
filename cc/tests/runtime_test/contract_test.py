"""Real ELF regression: matching libc, argument forwarding and exit propagation."""
from pathlib import Path
import os
import subprocess
import signal
import sys
import tempfile

wrapper = str(Path(sys.argv[1]).absolute())
for value, status in [("first argument", 0), ("second argument", 37)]:
    result = subprocess.run([wrapper, value, str(status)], capture_output=True, text=True)
    assert result.returncode == status, (result.returncode, result.stderr)
    assert result.stdout == "target-glibc=2.41 argument=" + value + "\n", result.stdout

missing = subprocess.run([str(Path(sys.argv[2]).absolute()), "unused", "0"], capture_output=True, text=True)
assert missing.returncode != 0 and "matching libc6 loader" in missing.stderr, missing
assert Path(sys.argv[3]).read_text() == "target-glibc=2.41 argument=exec tool\n"

with tempfile.TemporaryDirectory() as temporary:
    link = Path(temporary) / "original-layout-program"
    link.symlink_to(wrapper)
    result = subprocess.run([str(link), "linked argument", "0"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "target-glibc=2.41 argument=linked argument\n"

# FRR's upstream Memcheck scripts require the real target ELF, matching libc,
# and the instrument's error status, while preserving program arguments/exits.
instrumented = str(Path(sys.argv[4]).absolute())
for value, status in [("instrumented first argument", 0), ("instrumented second argument", 37)]:
    result = subprocess.run([instrumented, value, str(status)], capture_output=True, text=True)
    assert result.returncode == status, (result.returncode, result.stderr)
    assert result.stdout == "target-glibc=2.41 argument=" + value + "\n", result.stdout

memchecked = str(Path(sys.argv[5]).absolute())
for index, expected in [("0", 0), ("2", 86)]:
    result = subprocess.run([memchecked, index], capture_output=True, text=True)
    assert result.returncode == expected, (index, result.returncode, result.stderr)

# Original fuzz regression drivers use waitpid/WIFEXITED: a crashing target
# must remain signal-terminated when executed through this runtime wrapper.
for terminating_signal in (signal.SIGTERM, signal.SIGABRT, signal.SIGKILL):
    result = subprocess.run([wrapper, "raise-signal", str(terminating_signal)], capture_output=True, text=True)
    assert result.returncode == -terminating_signal, (result.returncode, result.stderr)

# Build generators can provide another Python on PATH and an embedded target
# PYTHONHOME. The helper must initialize its declared interpreter while leaving
# those target settings intact. An empty PATH also excludes /usr/bin/env Python.
foreign_environment = dict(os.environ, PATH="", PYTHONHOME="/missing/target-python", PYTHONPATH="/target/python/modules")
result = subprocess.run([wrapper, "python-environment", "0"], env=foreign_environment, capture_output=True, text=True)
assert result.returncode == 0, result.stderr
assert result.stdout == "/missing/target-python|/target/python/modules\n", result.stdout
