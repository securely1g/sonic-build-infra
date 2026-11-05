"""Source-built host tools preserve behavior with a declared execution runtime."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

probe, static_probe, gawk, swig, action_output = [str(Path(value).absolute()) for value in sys.argv[1:]]
with tempfile.TemporaryDirectory() as directory:
    environment = dict(os.environ, LD_LIBRARY_PATH=directory, LD_PRELOAD="", LD_AUDIT="")
    for executable in (probe, static_probe):
        def run(*args):
            return subprocess.run([executable, *args], cwd=directory, env=environment,
                                  text=True, capture_output=True)
        result = run("argument with spaces", "", "last")
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["source host runtime", directory, "argument with spaces", "", "last"]
        assert run("exit").returncode == 37
        assert run("signal").returncode == -signal.SIGTERM
    result = subprocess.run([gawk, "BEGIN { print 7 * 6 }"], cwd=directory,
                            env=environment, text=True, capture_output=True, check=True)
    assert result.stdout == "42\n", result
    result = subprocess.run([swig, "-version"], cwd=directory,
                            env=environment, text=True, capture_output=True, check=True)
    assert "SWIG Version" in result.stdout, result
assert Path(action_output).read_text() == "42\n"
