"""Compile the behavioral test using only queried flags and the selected SDK."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

spec = json.loads(Path(sys.argv[1]).read_text())
root = Path.cwd()
pc = root / spec["build_pc"]
apt_pc = root / spec["apt_pc"]
# Debian .pc paths are installation paths beneath the package's /usr tree.
apt_root = str(apt_pc).split("/usr/", 1)[0]
environment = {"PATH": "", "LANG": "C", "LC_ALL": "C", "PKG_CONFIG_PATH": "",
               "PKG_CONFIG_LIBDIR": os.pathsep.join([str(pc.parent), str(apt_pc.parent)]),
               "PKG_CONFIG_SYSROOT_DIR": apt_root, "PKG_CONFIG_FDO_SYSROOT_RULES": "1"}
pkgconf = str(root / spec["pkgconf"] / "bin/pkgconf")
version = subprocess.check_output([pkgconf, "--modversion", spec["module"]], env=environment, text=True).strip()
assert version == spec["version"], version
flags = shlex.split(subprocess.check_output(
    [pkgconf, "--static", "--cflags", "--libs", spec["module"], "json-c"],
    env=environment, text=True))
# No fixture CcInfo flags are injected here. A broken exported include or define
# must fail compilation, and the test executable checks both libraries' behavior.
native_dynamic = spec.get("native_dynamic", False)
binary = sys.argv[2] + ".native" if native_dynamic else sys.argv[2]
command = spec["compiler"] + ([] if native_dynamic else ["-static"]) + ["-no-pie", spec["source"], "-o", binary] + flags + spec["link_flags"]
result = subprocess.run(command, env=environment, capture_output=True, text=True)
if result.returncode:
    raise RuntimeError(json.dumps({"command": command, "stdout": result.stdout, "stderr": result.stderr}, indent=2))
print(json.dumps({"version": version, "flags": flags, "command": command}, indent=2))

if native_dynamic:
    environment["LD_LIBRARY_PATH"] = os.pathsep.join(str(root / path) for path in spec["library_dirs"])
    subprocess.run([str(root / binary)], env=environment, check=True)
    Path(binary).unlink()
    Path(sys.argv[2]).write_text("#!/bin/sh\nexit 0\n")
    Path(sys.argv[2]).chmod(0o755)
