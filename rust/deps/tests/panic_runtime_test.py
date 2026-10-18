"""Prove the selected Rust panic runtime while linking a real C++ dependency."""

import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile


def no_core_dump():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


with tempfile.TemporaryDirectory(prefix="rust-panic-runtime-") as directory:
    for binary, expected, unwinds in ((sys.argv[1], -signal.SIGABRT, False),
                                     (sys.argv[2], 101, True)):
        marker = Path(directory) / Path(binary).name
        result = subprocess.run([binary], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PANIC_DROP_FILE=str(marker), RUST_BACKTRACE="0"),
                                preexec_fn=no_core_dump)
        assert result.returncode == expected, (binary, result.returncode, result.stderr)
        assert "cpp=49" in result.stdout, result.stdout
        assert "controlled panic" in result.stderr, result.stderr
        assert marker.exists() == unwinds, (binary, marker.exists())
        if unwinds:
            assert marker.read_text() == "unwound"
print("Verified C++ linkage, panic abort without unwinding, and unchanged default unwinding.")
