#!/usr/bin/env python3
"""Check the installed native hello payload and its matching detached symbols."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import tarfile
import tempfile
import zlib


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True,
                            env={**os.environ, "LC_ALL": "C"})
    require(result.returncode == 0, "command failed: " + repr(args) + "\n" + result.stderr)
    return result.stdout


def read_archive(path):
    files = {}
    with tarfile.open(path) as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts,
                    "unsafe archive member: " + member.name)
            if member.isdir():
                require(member.mode == 0o755, "unexpected directory mode: " + member.name)
                continue
            require(member.isfile(), "unexpected archive member type: " + member.name)
            require(str(name) not in files, "duplicate archive member: " + member.name)
            files[str(name)] = (member, archive.extractfile(member).read())
    return files


def build_id(path):
    matches = re.findall(r"Build ID: ([0-9a-f]+)", command("readelf", "--notes", str(path)))
    require(len(matches) == 1, "expected one ELF build ID: " + str(path))
    return matches[0]


def verify(runtime_path, debug_path, source_data, architecture):
    runtime = read_archive(runtime_path)
    debug = read_archive(debug_path)
    require(set(runtime) == {"usr/bin/hello", "usr/share/file.txt"},
            "unexpected runtime archive inventory")
    for name, (member, _) in runtime.items():
        require(member.mode == 0o755 and member.uid == member.gid == 0,
                "runtime payload mode or ownership mismatch: " + name)
    require(runtime["usr/share/file.txt"][1] == source_data.read_bytes(),
            "installed data differs from the declared source")
    binary_data = runtime["usr/bin/hello"][1]
    require(binary_data[:6] == b"\x7fELF\x02\x01", "expected a little-endian ELF64 executable")
    require(struct.unpack_from("<H", binary_data, 18)[0] == {"amd64": 62, "arm64": 183}[architecture],
            "installed ELF has the wrong architecture")

    with tempfile.TemporaryDirectory(prefix="infra-deploy-archives-") as temporary:
        root = Path(temporary)
        binary = root / "usr/bin/hello"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(binary_data)
        binary.chmod(0o755)
        identifier = build_id(binary)
        symbol_name = "usr/lib/debug/.build-id/" + identifier[:2] + "/" + identifier[2:] + ".debug"
        require(set(debug) == {symbol_name}, "debug archive does not contain exactly the matching symbols")
        symbols = root / symbol_name
        symbols.parent.mkdir(parents=True)
        symbols.write_bytes(debug[symbol_name][1])
        require(build_id(symbols) == identifier, "runtime/debug build IDs differ")
        require(".debug_info" not in command("readelf", "--section-headers", "--wide", str(binary)),
                "runtime executable still contains DWARF")
        require(".debug_info" in command("readelf", "--section-headers", "--wide", str(symbols)),
                "detached symbols omit DWARF")
        link = root / "debuglink"
        command("objcopy", "--dump-section=.gnu_debuglink=" + str(link), str(binary), str(root / "copy"))
        raw = link.read_bytes()
        end = raw.index(0)
        require(raw[:end].decode() == symbols.name, "debuglink does not name the matching symbol file")
        offset = (end + 4) & ~3
        require(len(raw) == offset + 4, "malformed debuglink section")
        require(struct.unpack_from("<I", raw, offset)[0] == zlib.crc32(symbols.read_bytes()),
                "debuglink checksum differs from the shipped symbols")
        command(str(binary))
        gdb = command("gdb", "--nx", "--nh", "--batch",
                      "-iex", "set auto-load off", "-iex", "set debuginfod enabled off",
                      "-ex", "set debug-file-directory " + str(root / "usr/lib/debug"),
                      "-ex", "file " + str(binary), "-ex", "info line main")
        require(re.search(r'Line [1-9][0-9]* of "[^"]*hello\.c"', gdb),
                "GDB cannot find source lines through the installed debug tree")

    return {
        "architecture": architecture,
        "installed_files": sorted(runtime),
        "build_id": identifier,
        "debug_file": symbol_name,
        "installed_execution": "passed",
        "gdb": gdb.strip(),
        "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (runtime_path, debug_path)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--debug", type=Path, required=True)
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.runtime, args.debug, args.source_data, args.architecture), indent=2))


if __name__ == "__main__":
    main()
