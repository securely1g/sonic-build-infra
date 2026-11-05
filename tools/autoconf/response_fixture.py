"""Compile/link/run a static program through the real controlled CC launcher."""
import json
import os
from pathlib import Path
import shlex
import subprocess

cc = shlex.split(os.environ["CC"])
ar = shlex.split(os.environ["AR"])
directory = Path.cwd() / "paths with spaces"
directory.mkdir()
commands = []

def run(command):
    commands.append(command)
    subprocess.run(command, check=True)

sources = {
    "alpha": "int beta(void); int alpha(void) { return beta() + 1; }\n",
    "beta": "int beta(void) { return 41; }\n",
    "main": "int alpha(void); int main(void) { return alpha() != 42; }\n",
}
for name, text in sources.items():
    source = directory / (name + ".c")
    source.write_text(text)
    run(cc + ["-c", str(source), "-o", str(directory / (name + ".o"))])
for name in ("alpha", "beta"):
    run(ar + ["rcs", str(directory / (name + ".a")), str(directory / (name + ".o"))])
program = directory / "linked program"
run(cc + [str(directory / "main.o"), "-Wl,--start-group", str(directory / "alpha.a"), str(directory / "beta.a"), "-Xlinker", "--end-group", "-static", "-o", str(program)])
result = subprocess.run([str(program)], check=True)
Path("result.json").write_text(json.dumps({"program_exit_code": result.returncode, "commands": commands}, indent=2) + "\n")
