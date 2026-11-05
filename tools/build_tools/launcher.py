"""Invoke one pinned build tool using only its prepared private runtime."""

import json
import os
from pathlib import Path
import struct
import sys


def has_interpreter(binary):
    """Static ELF executables run directly; dynamic ELF needs the pinned loader."""
    with open(binary, "rb") as stream:
        header = stream.read(64)
        if header[:4] != b"\x7fELF":
            return False
        endian = "<" if header[5] == 1 else ">"
        if header[4] == 2:
            offset = struct.unpack_from(endian + "Q", header, 32)[0]
            entry_size, count = struct.unpack_from(endian + "HH", header, 54)
        elif header[4] == 1:
            offset = struct.unpack_from(endian + "I", header, 28)[0]
            entry_size, count = struct.unpack_from(endian + "HH", header, 42)
        else:
            raise ValueError("Unsupported ELF class")
        for index in range(count):
            stream.seek(offset + index * entry_size)
            if struct.unpack(endian + "I", stream.read(4))[0] == 3:  # PT_INTERP
                return True
    return False


def main():
    if sys.argv[1] == "--binary":
        binary, metadata, directory, *arguments = sys.argv[2:]
        metadata = Path(metadata)
        prepared = metadata.is_dir()
        manifest = json.loads((metadata / "toolchain.json" if prepared else metadata).read_text())
        expected_profile = "foreign" if prepared else "source"
        if manifest.get("format_version") != 1 or manifest.get("profile") != expected_profile:
            raise SystemExit("unsupported source-tool runtime")
        root = (metadata if prepared else Path(directory)).resolve()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("LD_")}
        if prepared:
            # A source-built embedder uses this runtime's APT Python ABI and
            # stdlib. Preserve caller-declared PYTHONPATH for generator inputs.
            environment.update(PYTHONHOME=str(root / "usr"), PYTHONNOUSERSITE="1")
        if not has_interpreter(binary):
            os.execve(binary, [binary] + arguments, environment)
        loader = str(root / manifest["loader"])
        libraries = os.pathsep.join(str(root / entry) for entry in manifest["library_directories"])
        os.execve(loader, [loader, "--inhibit-cache", "--library-path", libraries, binary] + arguments, environment)
    tool, directory, *arguments = sys.argv[1:]
    root = Path(directory).resolve()
    manifest = json.loads((root / "toolchain.json").read_text())
    if manifest.get("format_version") != 1 or tool not in manifest["tools"]:
        raise SystemExit("unsupported build-tool runtime")
    if manifest.get("profile") in ("configure", "kerberos", "foreign", "image", "manuals", "tcl", "gdb", "tests", "perl", "docbook"):
        executable = str(root / "bin" / tool)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("LD_")}
        if manifest.get("profile") == "tcl":
            environment = {key: value for key, value in environment.items() if not key.startswith("TCL")}
        if manifest.get("profile") == "manuals":
            environment = {key: value for key, value in environment.items() if not key.startswith("PYTHON")}
        os.execve(executable, [executable] + arguments, environment)
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
