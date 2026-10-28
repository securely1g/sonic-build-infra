"""Compare declared-runtime execution with real ELF loader search semantics."""
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from cc.runtime_test import run
from elf_metadata import Elf


program, middle, leaf, patchelf, payload = map(lambda name: Path(name).absolute(), sys.argv[1:])
with tempfile.TemporaryDirectory(prefix="origin-contract-") as temporary:
    root = Path(temporary) / "layout with spaces"
    (root / "lib/leaf").mkdir(parents=True)
    binary = root / "program"
    shared = root / "lib/liborigin_middle.so"
    child = root / "lib/leaf/liborigin_leaf.so"
    for source, target in [(program, binary), (middle, shared), (leaf, child)]:
        shutil.copyfile(source, target)
        target.chmod(0o755)
    runtime = root / "runtime"
    runtime.mkdir()
    with tarfile.open(payload) as archive:
        for member in archive:
            if member.isfile():
                data = archive.extractfile(member).read()
                if data.startswith(b"\x7fELF"):
                    soname = Elf(data).soname
                    if soname:
                        target = runtime / soname
                        target.write_bytes(data)
                        target.chmod(0o755)
    loader = next(runtime.glob("ld-linux-*"))

    def set_path(target, value, *, rpath=False):
        subprocess.run([str(patchelf), *(["--force-rpath"] if rpath else []), "--set-rpath", value, str(target)], check=True)

    def check(expected):
        # The control is the actual loader supplied by the declared libc6.
        control = subprocess.run([str(loader), "--inhibit-cache", "--library-path", str(runtime), str(binary)], capture_output=True)
        assert (control.returncode == 0) == expected, (control.returncode, control.stderr)
        if expected:
            assert run(binary, [payload], []) == 0
        else:
            try:
                run(binary, [payload], [])
            except ValueError as error:
                assert "undeclared runtime library: liborigin_leaf.so" in str(error), error
            else:
                raise AssertionError("An unresolved indirect dependency must be rejected")

    # A shared object's ORIGIN is its own directory, including braced tokens.
    set_path(binary, "$ORIGIN/lib")
    set_path(shared, "${ORIGIN}/leaf")
    check(True)
    set_path(shared, "$ORIGIN/leaf", rpath=True)
    check(True)

    # Main RPATH reaches grandchildren; main RUNPATH reaches only children.
    subprocess.run([str(patchelf), "--remove-rpath", str(shared)], check=True)
    set_path(binary, "$ORIGIN/lib:$ORIGIN/lib/leaf", rpath=True)
    check(True)
    set_path(shared, "$ORIGIN/missing")
    check(False)
    subprocess.run([str(patchelf), "--remove-rpath", str(shared)], check=True)
    subprocess.run([str(patchelf), "--remove-rpath", str(binary)], check=True)
    set_path(binary, "$ORIGIN/lib:$ORIGIN/lib/leaf")
    check(False)

    # A valid search path never supplies a missing declared file.
    set_path(shared, "$ORIGIN/leaf")
    child.unlink()
    check(False)
