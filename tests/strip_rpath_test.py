"""Verify deployed ELF rpaths and matching debug files for both tag kinds."""

import pathlib
import re
import subprocess
import sys
import tempfile
import zlib

readelf, objcopy, original, stripped, debug_dir, expected_tag, mode = sys.argv[1:]


def inspect(path, option):
    return subprocess.check_output([readelf, option, path], text=True)


def rpaths(path):
    return re.findall(r"\((RPATH|RUNPATH)\).*?\[(.*?)\]", inspect(path, "-d"))


original_paths = rpaths(original)
if expected_tag == "STATIC":
    assert not original_paths, original_paths
    assert "There is no dynamic section" in inspect(original, "-d")
    expected = []
else:
    assert len(original_paths) == 1, original_paths
    assert original_paths[0][0] == expected_tag, original_paths
    assert "_solib_test" in original_paths[0][1], original_paths
    assert "app.runfiles/workspace" in original_paths[0][1], original_paths
    expected = [] if mode == "all" else [(expected_tag, "$ORIGIN/../lib:/opt/sonic/lib")]
actual = rpaths(stripped)
assert actual == expected, (actual, expected)

runtime_id = re.search(r"Build ID: (\w+)", inspect(stripped, "-n")).group(1)
debug = pathlib.Path(debug_dir) / ".build-id" / runtime_id[:2] / (runtime_id[2:] + ".debug")
assert debug.is_file(), debug
assert re.search(r"Build ID: (\w+)", inspect(str(debug), "-n")).group(1) == runtime_id
with tempfile.TemporaryDirectory() as temporary:
    section = pathlib.Path(temporary) / "debuglink"
    subprocess.run(
        [objcopy, "--dump-section=.gnu_debuglink=" + str(section), stripped,
         str(pathlib.Path(temporary) / "copy")],
        check=True,
    )
    debuglink = section.read_bytes()
filename, _ = debuglink.split(b"\0", 1)
assert filename.decode() == debug.name, filename
crc_offset = (len(filename) + 4) & ~3
elf_data = pathlib.Path(stripped).read_bytes()[5]
assert elf_data in (1, 2), elf_data
byteorder = "little" if elf_data == 1 else "big"
assert len(debuglink) == crc_offset + 4, debuglink
assert int.from_bytes(debuglink[crc_offset:], byteorder) == zlib.crc32(debug.read_bytes())
subprocess.run([str(pathlib.Path(stripped).resolve())], check=True)
