"""Invoke one pinned build tool using only its prepared private runtime."""

import json
import os
from pathlib import Path
import sys


def main():
    tool, directory, *arguments = sys.argv[1:]
    root = Path(directory).resolve()
    manifest = json.loads((root / "toolchain.json").read_text())
    if manifest.get("format_version") != 1 or tool not in ("aspell", "doxygen"):
        raise SystemExit("unsupported build-tool runtime")
    loader = str(root / manifest["loader"])
    libraries = os.pathsep.join(str(root / entry) for entry in manifest["library_directories"])
    command = [loader, "--library-path", libraries, str(root / manifest["tools"][tool])]
    if tool == "aspell":
        command += [
            "--lang=en",
            "--data-dir=" + str(root / manifest["aspell_data_directory"]),
            "--dict-dir=" + str(root / manifest["aspell_dictionary_directory"]),
            "--add-filter-path=" + str(root / manifest["aspell_filter_directory"]),
        ]
    # Keep the caller's working directory, standard streams, locale and HOME:
    # tools may read caller-owned inputs, including Aspell's relative -p file.
    # Host loader overrides must not replace the declared runtime libraries.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("LD_")}
    os.execve(loader, command + arguments, environment)


if __name__ == "__main__":
    main()
