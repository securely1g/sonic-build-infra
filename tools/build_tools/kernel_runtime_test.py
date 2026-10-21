"""Run the declared compiler and Debian packaging tools in their own rootfs."""

import json
import os
import platform
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile


def main():
    if os.geteuid() != 0:
        raise SystemExit("kernel_runtime_test requires root in a disposable Docker worker with CAP_SYS_CHROOT and CAP_MKNOD")
    runtime = Path(sys.argv[1]).resolve()
    manifest = json.loads((runtime / "kernel-runtime.json").read_text())
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    assert manifest["architecture"] == architecture
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
        # Exercise the SAX registry and real XML parsing, not just module loading.
        assert run([
            "/usr/bin/perl", "-MXML::Simple", "-MXML::SAX::ParserFactory", "-e",
            'my $factory = XML::SAX::ParserFactory->new(); '
            '$factory->require_feature("http://xml.org/sax/features/namespaces"); '
            '$factory->parser(); '
            'my $data = XMLin(q{<root><value>declared &amp; parsed</value></root>}); '
            'print $data->{value};',
        ]) == "declared & parsed"
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
        native_elf = (work / "host-tools").read_bytes()
        assert native_elf[:5] == b"\x7fELF\x02", "expected a native ELF64 executable"
        assert struct.unpack_from("<H", native_elf, 18)[0] == {"amd64": 62, "arm64": 183}[architecture]
        compatibility_vdso = None
        if architecture == "arm64":
            # Native ARM64 kernel builds also compile the 32-bit compatibility
            # vDSO. Exercise that compiler and linker without executing ARM32 code.
            run(["/usr/bin/dpkg-checkbuilddeps", "-d", "gcc-arm-linux-gnueabihf"])
            (work / "compat-vdso.c").write_text("int compat_probe(void) { return 0; }\n")
            run(["/usr/bin/arm-linux-gnueabihf-gcc", "-fPIC", "-mabi=aapcs-linux",
                 "-mfloat-abi=soft", "-nostdlib", "-shared", "compat-vdso.c", "-o", "compat-vdso.so"])
            compat_elf = (work / "compat-vdso.so").read_bytes()
            assert compat_elf[:5] == b"\x7fELF\x01", "expected an ELF32 compatibility vDSO"
            assert struct.unpack_from("<H", compat_elf, 18)[0] == 40, "expected ARM machine type"
            compatibility_vdso = "ARM ELF32 compile/link passed"
        dependencies = run(["/usr/bin/dpkg-shlibdeps", "-O", "host-tools"])
        for package in ("libc6", "libelf1t64", "libssl3t64"):
            assert package in dependencies, dependencies
        run(["/usr/bin/dpkg-source", "--version"])
        run(["/usr/bin/dh_testdir"])
        print(json.dumps({"result": "passed", "architecture": architecture, "compatibility_vdso": compatibility_vdso, "runtime_identity": manifest["identity_sha256"], "dependencies": dependencies.strip(), "quilt_patch": "push/pop passed", "vendor": "Debian"}, sort_keys=True))


if __name__ == "__main__":
    main()
