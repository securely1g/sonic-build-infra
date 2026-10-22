"""Run relocated declared kernel tools without root or a replacement filesystem."""

import io
import json
import os
import platform
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile


def check_linker_inputs(root, runtime, work, run, architecture):
    """Exercise linker scripts materialized as Bazel-style input symlinks."""
    triple = {"amd64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu"}[architecture]
    directory = root / "usr/lib" / triple
    mode = directory.stat().st_mode & 0o777
    directory.chmod(mode | 0o200)
    try:
        for name in ("libc.so", "libm.so", "libm.a"):
            script = directory / name
            script.unlink()
            script.symlink_to(runtime / "usr/lib" / triple / name)
    finally:
        directory.chmod(mode)
    (work / "link-inputs.c").write_text(
        "#include <math.h>\n#include <openssl/crypto.h>\n"
        "int main(int argc, char **argv) { return OpenSSL_version_num() == 0 || cos((double)argc) > 2; }\n")
    (work / "link-math.c").write_text(
        "#include <math.h>\nint main(int argc, char **argv) { return cos((double)argc) > 2; }\n")
    for compiler in ("gcc", "host-gcc"):
        for math in ("shared", "static"):
            output = "link-inputs-" + compiler + "-" + math
            flags = ["link-inputs.c", "-lcrypto", "-lm"] if math == "shared" else ["-static", "link-math.c", "-lm"]
            run(["/usr/bin/" + compiler, "-fno-builtin", *flags,
                 "-Wl,-Map," + output + ".map", "-Wl,--dependency-file," + output + ".d", "-o", output])
            inputs = re.findall(r"^LOAD (.+)$", (work / (output + ".map")).read_text(), re.MULTILINE)
            dependencies = (work / (output + ".d")).read_text()
            assert not any(path.startswith(("/usr/", "/lib/", "/lib64/")) for path in inputs), inputs
            assert not re.search(r"(?:^|\s)/(?:usr/|lib/|lib64/)", dependencies), dependencies
            libraries = (["libcrypto.so", "libc.so.6", "libc_nonshared.a", "libm.so.6", "libmvec.so.1"]
                         if math == "shared" else ["libc.a", "libm-2.41.a", "libmvec.a"])
            for library in libraries:
                paths = [path for path in inputs if path.endswith("/" + library)]
                assert paths and all(path.startswith(str(root) + "/") for path in paths), (library, paths)
                assert all(path in dependencies for path in paths), (library, dependencies)
    return "gcc/host-gcc shared/static libc/libm/crypto inputs stay in the declared sysroot"


def check_debian_package(root, work, environment, run, architecture):
    """Exercise the maintained Debian packaging sequence without privileges."""
    (work / "kernel_tools_check.py").write_text("VALUE = 42\n")
    (work / "debian/source").mkdir()
    (work / "debian/source/format").write_text("3.0 (native)\n")
    (work / "debian/copyright").write_text("Test fixture; public domain.\n")
    (work / "debian/changelog").write_text(
        "kernel-tools-check (1.0) unstable; urgency=medium\n\n"
        "  * Test the declared packaging tools.\n\n"
        " -- SONiC <sonic@example.invalid>  Thu, 01 Jan 1970 00:00:00 +0000\n")
    (work / "debian/rules").write_text("""#!/usr/bin/make -f
SHELL := $(SONIC_BUILD_TOOLS_ROOT)/bin/bash
.PHONY: clean build build-arch build-indep binary binary-arch binary-indep
clean:
	dh_testdir
	dh_clean
	rm -f package-tools
build: build-arch build-indep
build-arch:
	gcc -g -O2 -fdebug-prefix-map=$(CURDIR)=. host-tools.c -lelf -lcrypto -o package-tools
build-indep:
binary: binary-arch binary-indep
binary-arch: build-arch
	dh_testdir
	dh_testroot
	dh_prep
	install -D -m755 package-tools debian/kernel-tools-check/usr/bin/kernel-tools-check
	install -D -m644 kernel_tools_check.py debian/kernel-tools-check/usr/lib/python3/dist-packages/kernel_tools_check.py
	dh_installdocs
	dh_installchangelogs
	dh_compress
	dh_strip
	dh_fixperms
	dh_python3
	dh_shlibdeps
	dh_gencontrol
	dh_md5sums
	dh_builddeb
binary-indep:
""")
    (work / "debian/rules").chmod(0o755)
    run(["/usr/bin/dpkg-buildpackage", "-b", "-us", "-uc", "-j2",
         "--rules-file=make -f debian/rules"])
    package = work.parent / f"kernel-tools-check_1.0_{architecture}.deb"
    debug_package = work.parent / f"kernel-tools-check-dbgsym_1.0_{architecture}.deb"
    assert run(["/usr/bin/dpkg-deb", "--field", str(package), "Architecture"]).strip() == architecture
    dependencies = run(["/usr/bin/dpkg-deb", "--field", str(package), "Depends"])
    for dependency in ("libc6", "libelf1t64", "libssl3t64", "python3"):
        assert dependency in dependencies, dependencies

    def members(archive):
        result = subprocess.run([str(root / "bin/dpkg-deb"), "--fsys-tarfile", str(archive)],
                                cwd=work, env=environment, capture_output=True, check=True)
        with tarfile.open(fileobj=io.BytesIO(result.stdout)) as payload:
            assert all(member.uid == member.gid == 0 for member in payload.getmembers())
            return {member.name.removeprefix("./"): payload.extractfile(member).read()
                    for member in payload.getmembers() if member.isfile()}

    files = members(package)
    assert files["usr/lib/python3/dist-packages/kernel_tools_check.py"] == b"VALUE = 42\n"
    installed = work / "installed-program"
    installed.write_bytes(files["usr/bin/kernel-tools-check"])
    assert ".debug_info" not in run(["/usr/bin/readelf", "--sections", str(installed)])
    debug_files = [data for name, data in members(debug_package).items() if name.endswith(".debug")]
    assert len(debug_files) == 1
    debug = work / "installed.debug"
    debug.write_bytes(debug_files[0])
    assert ".debug_info" in run(["/usr/bin/readelf", "--sections", str(debug)])
    return "runtime/dbgsym, native/Python dependencies and root ownership passed"


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
        (work / "debian/control").write_text(
            "Source: kernel-tools-check\nSection: devel\nPriority: optional\n"
            "Maintainer: SONiC <sonic@example.invalid>\nRules-Requires-Root: no\n"
            "Build-Depends: debhelper-compat (= 13), gcc-14, python3, dh-python, libelf-dev, libssl-dev\n\n"
            "Package: kernel-tools-check\nArchitecture: any\n"
            "Depends: ${shlibs:Depends}, ${misc:Depends}, ${python3:Depends}\n"
            "Description: Build tools smoke check\n")
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
                       "MAGIC": "/undeclared-magic-must-not-be-used",
                       "LD_LIBRARY_PATH": "/undeclared-libraries-must-not-be-used"}

        def run(command):
            if command[0].startswith(("/usr/bin/", "/bin/")):
                command = [str(root / "bin" / Path(command[0]).name), *command[1:]]
            result = subprocess.run(command, cwd=work, env=environment, text=True, capture_output=True)
            if result.returncode:
                sys.stderr.write(result.stdout + result.stderr)
                result.check_returncode()
            return result.stdout

        linker_inputs = check_linker_inputs(root, runtime, work, run, architecture)
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
        # Poison the raw awk alias in this private runtime copy. The declared
        # launcher selects mawk directly, but Quilt's old compatibility symlink
        # bypassed that launcher. Catch the bypass even on a newer host libc.
        raw_awk = root / "usr/bin/awk"
        saved_awk = root / "usr/bin/awk.test-saved"
        directory_mode = raw_awk.parent.stat().st_mode & 0o777
        raw_awk.parent.chmod(directory_mode | 0o200)
        raw_awk.rename(saved_awk)
        raw_awk.write_text('#!/bin/sh\necho "quilt bypassed the declared awk launcher" >&2\nexit 97\n')
        raw_awk.chmod(0o755)
        try:
            run(["/usr/bin/quilt", "--quiltrc", "/dev/null", "push", "-a"])
            assert patch_input.read_text() == "after\n"
            run(["/usr/bin/quilt", "--quiltrc", "/dev/null", "pop", "-a"])
            assert patch_input.read_text() == "before\n"
        finally:
            raw_awk.unlink()
            saved_awk.rename(raw_awk)
            raw_awk.parent.chmod(directory_mode)
        run(["/usr/bin/host-gcc", "host-tools.c", "-lelf", "-lcrypto", "-o", "host-tools"])
        run(["./host-tools"])
        (work / "cxx-tools.cc").write_text("#include <vector>\nint main() { std::vector<int> v{1, 2}; return v.size() != 2; }\n")
        run(["/usr/bin/host-g++", "cxx-tools.cc", "-o", "cxx-tools"])
        run(["./cxx-tools"])
        run(["/usr/bin/gcc", "host-tools.c", "-lelf", "-lcrypto", "-o", "deployment-tools"])
        # Debhelper uses file to select ELF inputs for stripping and shlibdeps.
        # A host magic database can silently misclassify them and drop Depends.
        assert "ELF 64-bit" in run(["/usr/bin/file", "--brief", "deployment-tools"])
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
        package_check = check_debian_package(root, work, environment, run, architecture)
        print(json.dumps({"result": "passed", "architecture": architecture, "execution": "relocatable", "uid": os.geteuid(), "compatibility_vdso": compatibility_vdso, "runtime_identity": manifest["identity_sha256"], "dependencies": dependencies.strip(), "quilt_patch": "push/pop passed", "linker_inputs": linker_inputs, "debian_package": package_check, "vendor": "Debian"}, sort_keys=True))


if __name__ == "__main__":
    main()
