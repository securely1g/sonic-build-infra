"""A source-built host tool must run with its declared execution runtime."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

patchelf, source = map(lambda value: str(Path(value).absolute()), sys.argv[1:])
with tempfile.TemporaryDirectory() as directory:
    binary = Path(directory) / "binary with spaces"
    shutil.copyfile(source, binary)
    # Ambient loader overrides must not replace the tool's declared libraries.
    environment = dict(os.environ, LD_LIBRARY_PATH=directory, LD_PRELOAD="", LD_AUDIT="")
    def run(*arguments, **kwargs):
        return subprocess.run([patchelf, *map(str, arguments)], cwd=directory,
                              env=environment, text=True, capture_output=True, **kwargs)
    run("--set-rpath", "/example/one:/example/path with spaces", binary, check=True)
    assert run("--print-rpath", binary, check=True).stdout.strip() == "/example/one:/example/path with spaces"
    run("--remove-rpath", binary, check=True)
    assert run("--print-rpath", binary, check=True).stdout.strip() == ""
    invalid = Path(directory) / "invalid"
    invalid.write_text("not an ELF file\n")
    assert run("--print-rpath", invalid).returncode != 0
