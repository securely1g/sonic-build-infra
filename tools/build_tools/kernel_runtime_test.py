"""Run the declared compiler and Debian packaging tools in their own rootfs."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


def main():
    if os.geteuid() != 0:
        raise SystemExit("kernel_runtime_test requires root in a disposable Docker worker with CAP_SYS_CHROOT and CAP_MKNOD")
    runtime = Path(sys.argv[1]).resolve()
    manifest = json.loads((runtime / "kernel-runtime.json").read_text())
    assert manifest["architecture"] == "amd64"
    assert manifest["kind"] == "debian-build-tools"
    with tempfile.TemporaryDirectory(prefix="kernel-runtime-check-") as temporary:
        root = Path(temporary) / "root"
        shutil.copytree(runtime, root, symlinks=True)
        os.mknod(root / "dev/null", stat.S_IFCHR | 0o666, os.makedev(1, 3))
        work = root / "work"
        (work / "debian").mkdir(parents=True)
        (work / "debian/control").write_text("Source: kernel-tools-check\nSection: devel\nPriority: optional\nMaintainer: SONiC <sonic@example.invalid>\n\nPackage: kernel-tools-check\nArchitecture: any\nDescription: Build tools smoke check\n")
        source = work / "host-tools.c"
        source.write_text("#include <libelf.h>\n#include <openssl/crypto.h>\nint main(void) { return elf_version(EV_CURRENT) == EV_NONE || OpenSSL_version_num() == 0; }\n")

        def enter():
            os.chroot(root)
            os.chdir("/work")

        def run(command):
            result = subprocess.run(command, preexec_fn=enter, env={"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "LANG": "C", "LC_ALL": "C", "HOME": "/tmp", "SOURCE_DATE_EPOCH": "0"}, text=True, capture_output=True)
            if result.returncode:
                sys.stderr.write(result.stdout + result.stderr)
                result.check_returncode()
            return result.stdout

        run(["/usr/bin/python3", "-c", "import dacite; import jinja2"])
        assert run(["/usr/bin/dpkg-vendor", "--query", "Vendor"]).strip() == "Debian"
        for command in ("gcc", "make", "dpkg-source", "dh_testdir", "quilt", "bc", "bison", "flex", "pahole", "cpio", "kmod", "rsync", "lz4", "zstd"):
            run(["/bin/sh", "-c", 'command -v "$1"', "--", command])
        # Quilt invokes Essential-package helpers such as getopt while applying
        # the kernel patch series; finding the quilt executable alone is not enough.
        patch_input = work / "quilt-input.txt"
        patch_input.write_text("before\n")
        patches = work / "patches"
        patches.mkdir()
        (patches / "series").write_text("runtime.patch\n")
        (patches / "runtime.patch").write_text("--- a/quilt-input.txt\n+++ b/quilt-input.txt\n@@ -1 +1 @@\n-before\n+after\n")
        run(["/usr/bin/quilt", "--quiltrc", "/dev/null", "push", "-a"])
        assert patch_input.read_text() == "after\n"
        run(["/usr/bin/quilt", "--quiltrc", "/dev/null", "pop", "-a"])
        assert patch_input.read_text() == "before\n"
        run(["/usr/bin/gcc", "host-tools.c", "-lelf", "-lcrypto", "-o", "host-tools"])
        run(["./host-tools"])
        dependencies = run(["/usr/bin/dpkg-shlibdeps", "-O", "host-tools"])
        for package in ("libc6", "libelf1t64", "libssl3t64"):
            assert package in dependencies, dependencies
        run(["/usr/bin/dpkg-source", "--version"])
        run(["/usr/bin/dh_testdir"])
        print(json.dumps({"result": "passed", "runtime_identity": manifest["identity_sha256"], "dependencies": dependencies.strip(), "quilt_patch": "push/pop passed", "vendor": "Debian"}, sort_keys=True))


if __name__ == "__main__":
    main()
