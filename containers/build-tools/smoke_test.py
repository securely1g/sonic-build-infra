#!/usr/bin/env python3
"""Exercise installed tools, including Aspell's generated English dictionaries."""

import json
from pathlib import Path
import platform
import subprocess

from identity import recipe_sha256


def main():
    marker = json.loads(Path("/etc/sonic-build-tools.json").read_text())
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    assert marker["schema_version"] == 1
    assert marker["architecture"] == architecture, "image must execute on its native architecture"
    assert marker["recipe_sha256"] == recipe_sha256(Path(__file__).resolve().parent)
    assert subprocess.check_output(["/usr/bin/doxygen", "--version"], text=True).strip() == "1.9.8"
    assert subprocess.check_output(["bazel", "--version"], text=True).strip() == "bazel 8.5.1"
    # Upstream SAI uses the installed absolute path and the default English data.
    words = subprocess.check_output(
        ["/usr/bin/aspell", "--lang=en", "list"], input="hello\nzzzznotaword\n", text=True,
    )
    assert words.splitlines() == ["zzzznotaword"], words
    subprocess.run(["perl", "-MData::Dumper", "-MIPC::Open2", "-e", "print qq(perl runtime OK\\n)"], check=True)
    for executable in ("gcc", "g++", "make", "readelf", "gdb", "git", "curl", "tar", "xz"):
        subprocess.run([executable, "--version"], check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({"architecture": architecture, "recipe_sha256": marker["recipe_sha256"], "result": "passed"}))


if __name__ == "__main__":
    main()
