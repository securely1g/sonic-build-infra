"""Exercise the real static bootstrap over a pre-existing package file alias."""

from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

from prepare_runtime import validate_launcher, write_launcher


def assert_static(executable):
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    validate_launcher(executable, architecture)
    data = executable.read_bytes()
    offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size, count = struct.unpack_from("<HH", data, 54)
    for index in range(count):
        header = struct.unpack_from("<IIQQQQQQ", data, offset + index * entry_size)
        assert header[0] != 3, "static launcher has a PT_INTERP dynamic loader"
        if header[0] == 2:  # PT_DYNAMIC; static PIE may have relocations only.
            dynamic = data[header[2]:header[2] + header[5]]
            for tag, _ in struct.iter_unpack("<qQ", dynamic):
                assert tag != 1, "static launcher has a DT_NEEDED shared library"
                if tag == 0:
                    break


def main():
    executable = Path(sys.argv[1]).resolve()
    assert_static(executable)
    with tempfile.TemporaryDirectory(prefix="runtime launcher-") as temporary:
        root = Path(temporary)
        (root / "bin").mkdir()
        original = root / "bin/tool-v1"
        original.write_bytes(b"original package payload\n")
        (root / "bin/tool").symlink_to(original.name)
        write_launcher(root, "tool", '#!/bin/sh\nprintf "%s\\n" "$1"\nexit 31\n', executable)
        assert original.read_bytes() == b"original package payload\n", "overwrote an aliased package file"
        result = subprocess.run([str(root / "bin/tool"), "argument with spaces"],
                                env={"PATH": ""}, capture_output=True, text=True)
        assert result.returncode == 31 and result.stdout == "argument with spaces\n", result
    print("Static ELF verified; existing package alias preserved; launcher uses its own sidecar")


if __name__ == "__main__":
    main()
