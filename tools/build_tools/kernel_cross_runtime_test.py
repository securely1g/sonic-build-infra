"""Exercise AMD64 compiler tools and target libraries for Debian kernel builds."""

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
    runtime = Path(sys.argv[1]).resolve()
    target = sys.argv[2]
    triple, machine, elf_class = {
        "arm64": ("aarch64-linux-gnu", 183, 2),
        "armhf": ("arm-linux-gnueabihf", 40, 1),
    }[target]
    assert platform.machine() == "x86_64"
    marker = json.loads((runtime / "kernel-runtime.json").read_text())
    assert marker["architecture"] == "amd64"
    assert marker["cross_architectures"] == ["arm64", "armhf"]
    assert "target_architecture" not in marker
    with tempfile.TemporaryDirectory(prefix="kernel-cross-tools-") as temporary:
        work = Path(temporary)
        database = work / "dpkg"
        shutil.copytree(runtime / "var/lib/dpkg", database, copy_function=shutil.copyfile)
        for listing in (database / "info").glob("*.list"):
            listing.write_text("\n".join(str(runtime) + line if line.startswith("/") else line
                                         for line in listing.read_text().splitlines()) + "\n")
        environment = {"PATH": str(runtime / "bin"), "HOME": str(work), "TMPDIR": str(work),
                       "LANG": "C", "LC_ALL": "C", "DPKG_ADMINDIR": str(database),
                       "DEB_RULES_REQUIRES_ROOT": "no"}

        def run(command):
            result = subprocess.run(command, cwd=work, env=environment, text=True, capture_output=True)
            if result.returncode:
                sys.stderr.write(result.stdout + result.stderr)
                result.check_returncode()
            return result.stdout

        assert run(["dpkg", "--print-foreign-architectures"]).splitlines() == ["arm64", "armhf"]
        native_pkgconfig = run(["pkg-config", "--libs", "libelf", "libcrypto"])
        assert str(runtime / "usr/lib/x86_64-linux-gnu") in native_pkgconfig
        assert "aarch64-linux-gnu" not in native_pkgconfig and "arm-linux-gnueabihf" not in native_pkgconfig
        (work / "probe.c").write_text(
            "#include <libelf.h>\n#include <openssl/crypto.h>\n"
            "int main(void) { return elf_version(EV_CURRENT) == EV_NONE || OpenSSL_version_num() == 0; }\n")
        run(["host-gcc", "probe.c", "-lelf", "-lcrypto", "-o", "generator"])
        run([str(work / "generator")])
        host = (work / "generator").read_bytes()
        assert struct.unpack_from("<H", host, 18)[0] == 62
        run([triple + "-gcc", "probe.c", "-lelf", "-lcrypto", "-Wl,-Map,probe.map", "-o", "probe"])
        binary = (work / "probe").read_bytes()
        assert binary[:5] == b"\x7fELF" + bytes([elf_class])
        assert struct.unpack_from("<H", binary, 18)[0] == machine
        assert str(runtime / "usr/lib" / triple) in (work / "probe.map").read_text()
        assert str(runtime) not in run([triple + "-readelf", "-l", "-d", "probe"])
        if target == "armhf":
            assert "hard-float ABI" in run([triple + "-readelf", "-h", "probe"])
        # Kernel NEON sources use a soft-float calling convention and compiler
        # builtin headers, even though user-space packages use the hard-float
        # ABI. Libc's stdint.h must not shadow GCC's freestanding implementation.
        builtin_headers = run([triple + "-gcc", "-print-file-name=include"]).strip()
        (work / "freestanding.c").write_text(
            "#include <arm_neon.h>\nuint32x4_t probe(void) { return vdupq_n_u32(1); }\n")
        flags = ["-mfloat-abi=softfp", "-mfpu=neon"] if target == "armhf" else []
        run([triple + "-gcc", "-ffreestanding", "-nostdinc", "-isystem", builtin_headers,
             "-MD", "-MF", "freestanding.d", "-c", "freestanding.c", "-o", "freestanding.o"] + flags)
        assert str(runtime / "usr/include") not in (work / "freestanding.d").read_text()
        assert str(runtime / "usr/lib" / triple) in run([triple + "-pkg-config", "--libs", "libelf", "libcrypto"])
        for line in run(["dpkg-architecture", "-a" + target, "-l"]).splitlines():
            key, value = line.split("=", 1)
            environment[key] = value
        (work / "debian").mkdir()
        (work / "debian/control").write_text(
            "Source: kernel-cross-tools-check\n"
            "Build-Depends: gcc-14-" + triple + ", libelf-dev:native, libelf-dev, libssl-dev:native, libssl-dev\n\n"
            "Package: kernel-cross-tools-check\nArchitecture: any\nDescription: Kernel tool test\n")
        run(["dpkg-checkbuilddeps", "-a" + target, "-Pcross,nocheck,nodoc"])
        dependencies = run(["dpkg-shlibdeps", "-O", "probe"])
        for name in ("libc6", "libelf1t64", "libssl3t64"):
            assert name in dependencies, dependencies
        print(json.dumps({"execution_architecture": "amd64", "target_architecture": target,
                          "cross_architectures": marker["cross_architectures"], "runtime_path": str(runtime),
                          "elf_machine": machine, "dependency_check": dependencies.strip(),
                          "build_tools_identity": marker["identity_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
