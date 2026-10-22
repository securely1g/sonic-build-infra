"""Run relocated declared kernel tools without root or a replacement filesystem."""

import json
import os
import platform
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile


def main():
    runtime = Path(sys.argv[1]).resolve()
    manifest = json.loads((runtime / "kernel-runtime.json").read_text())
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    assert manifest["architecture"] == architecture
    assert manifest["kind"] == "debian-build-tools"
    assert manifest["execution"] == "relocatable"
    with tempfile.TemporaryDirectory(prefix="kernel-runtime-check-") as temporary:
        root = Path(temporary) / "relocated-runtime"
        shutil.copytree(runtime, root, symlinks=True)
        work = Path(temporary) / "work"
        (work / "debian").mkdir(parents=True)
        (work / "debian/control").write_text("Source: kernel-tools-check\nSection: devel\nPriority: optional\nMaintainer: SONiC <sonic@example.invalid>\n\nPackage: kernel-tools-check\nArchitecture: any\nDescription: Build tools smoke check\n")
        source = work / "host-tools.c"
        source.write_text("#include <libelf.h>\n#include <openssl/crypto.h>\nint main(void) { return elf_version(EV_CURRENT) == EV_NONE || OpenSSL_version_num() == 0; }\n")

        database = work / "dpkg"
        shutil.copytree(root / "var/lib/dpkg", database, copy_function=shutil.copyfile)
        for listing in (database / "info").glob("*.list"):
            listing.write_text("\n".join(str(root) + line if line.startswith("/") else line
                                         for line in listing.read_text().splitlines()) + "\n")
        environment = {"PATH": str(root / "bin"), "LANG": "C", "LC_ALL": "C",
                       "HOME": str(work), "TMPDIR": str(work), "SOURCE_DATE_EPOCH": "0",
                       "DPKG_ADMINDIR": str(database), "DEB_RULES_REQUIRES_ROOT": "no",
                       "LD_LIBRARY_PATH": "/undeclared-libraries-must-not-be-used"}

        def run(command):
            if command[0].startswith(("/usr/bin/", "/bin/")):
                command = [str(root / "bin" / Path(command[0]).name), *command[1:]]
            result = subprocess.run(command, cwd=work, env=environment, text=True, capture_output=True)
            if result.returncode:
                sys.stderr.write(result.stdout + result.stderr)
                result.check_returncode()
            return result.stdout

        run(["/usr/bin/python3", "-c", "import dacite; import jinja2"])
        run(["/usr/bin/python3", "-c",
             "import sys; from debpython.version import DEFAULT; from dhpython.version import default; "
             "assert DEFAULT == sys.version_info[:2]; "
             "assert str(default('cpython3')) == '%d.%d' % sys.version_info[:2]"])
        for command in ("dh_python3", "py3compile", "py3clean"):
            run(["/usr/bin/" + command, "--help"])
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
        run(["/usr/bin/host-gcc", "host-tools.c", "-lelf", "-lcrypto", "-o", "host-tools"])
        run(["./host-tools"])
        (work / "cxx-tools.cc").write_text("#include <vector>\nint main() { std::vector<int> v{1, 2}; return v.size() != 2; }\n")
        run(["/usr/bin/host-g++", "cxx-tools.cc", "-o", "cxx-tools"])
        run(["./cxx-tools"])
        run(["/usr/bin/gcc", "host-tools.c", "-lelf", "-lcrypto", "-o", "deployment-tools"])
        # Only build-time programs receive a private interpreter/RPATH. The
        # normal compiler must still produce the Debian deployment interface.
        assert str(root) in run(["/usr/bin/readelf", "--program-headers", "host-tools"])
        assert str(root) not in run(["/usr/bin/readelf", "--program-headers", "deployment-tools"])
        assert str(root) not in run(["/usr/bin/readelf", "--dynamic", "deployment-tools"])
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
        dependencies = run(["/usr/bin/dpkg-shlibdeps", "-O", "-l" + str(root / "usr/lib" / {"amd64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu"}[architecture]), "host-tools"])
        for package in ("libc6", "libelf1t64", "libssl3t64"):
            assert package in dependencies, dependencies
        run(["/usr/bin/dpkg-source", "--version"])
        run(["/usr/bin/dh_testdir"])
        run(["/usr/bin/dh_testroot"])
        print(json.dumps({"result": "passed", "architecture": architecture, "execution": "relocatable", "uid": os.geteuid(), "compatibility_vdso": compatibility_vdso, "runtime_identity": manifest["identity_sha256"], "dependencies": dependencies.strip(), "quilt_patch": "push/pop passed", "vendor": "Debian"}, sort_keys=True))


if __name__ == "__main__":
    main()
