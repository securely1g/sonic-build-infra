"""Run both Debian generators after relocating their entire private runtime."""

import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile


def main():
    source = Path(sys.argv[1]).resolve()
    manifest = json.loads((source / "toolchain.json").read_text())
    architecture, machine = {"x86_64": ("amd64", 62), "aarch64": ("arm64", 183)}[platform.machine()]
    assert manifest["architecture"] == architecture and manifest["profile"] == "kerberos"
    for name in ("mawk", "sed", "grep", "basename", "cmp", "rm", "mv", "chmod"):
        assert struct.unpack_from("<H", (source / "usr/bin" / name).read_bytes(), 18)[0] == machine
    with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as temporary:
        root = Path(temporary)
        bundle = root / "relocated-tools"
        shutil.copytree(source, bundle, symlinks=True)
        environment = {"PATH": "", "LC_ALL": "C", "LANG": "C", "HOME": str(root),
                       "LD_LIBRARY_PATH": "/nonexistent-host-libraries",
                       "_ET_DIR_OVERRIDE": "/nonexistent-host-error-tables",
                       "_SS_DIR_OVERRIDE": "/nonexistent-host-command-tables",
                       "SONIC_BUILD_TOOLS_AWK": "/nonexistent-host-awk"}
        (root / "sample.et").write_text('error_table demo\nerror_code SAMPLE_ERROR, "Declared generator works"\nend\n')
        (root / "sample.ct").write_text('command_table sample;\nrequest sample_command, "Declared command works", sample;\nend;\n')

        def run(name, argument, success=True):
            result = subprocess.run([str(bundle / "bin" / name), argument], cwd=root, env=environment,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            assert (result.returncode == 0) == success, (name, result.returncode, result.stdout)
            return result.stdout

        for executable, argument in zip(sys.argv[2:], ("sample.et", "sample.ct")):
            subprocess.run([str(Path(executable).absolute()), argument], cwd=root, env=environment, check=True)
        run("compile_et", "sample.et")
        error_c, error_h = (root / "sample.c").read_text(), (root / "sample.h").read_text()
        assert "Declared generator works" in error_c and "SAMPLE_ERROR" in error_h
        run("compile_et", "sample.et")  # Exercise the generated-file comparison path.
        assert (root / "sample.c").read_text() == error_c and (root / "sample.h").read_text() == error_h
        run("mk_cmds", "sample.ct")
        commands = (root / "sample.c").read_text()
        assert "sample_command" in commands and "Declared command works" in commands
        run("mk_cmds", "sample.ct")
        assert (root / "sample.c").read_text() == commands
        (bundle / "usr/share/ss/ct_c.awk").unlink()
        assert "Couldn't find" in run("mk_cmds", "sample.ct", success=False)
        (bundle / "usr/share/et/et_h.awk").unlink()
        assert "Couldn't find" in run("compile_et", "sample.et", success=False)
    print("Native compile_et and mk_cmds generated stable C after relocation, without host PATH or templates")


if __name__ == "__main__":
    main()
