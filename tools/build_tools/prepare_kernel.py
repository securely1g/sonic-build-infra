"""Make the declared Debian kernel tools runnable without a replacement root."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess

from kernel_debian import prepare_debian
from prepare_runtime import library_directories, loader_path


ARCHITECTURES = {
    "amd64": ("x86_64-linux-gnu", 62),
    "arm64": ("aarch64-linux-gnu", 183),
}


def _header(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(256)


def _native_libraries(root: Path, machine: int) -> list[Path]:
    directories = []
    for directory in library_directories(root):
        for path in directory.glob("*.so*"):
            if not path.is_file():
                continue
            header = _header(path)
            if header[:4] == b"\x7fELF" and len(header) >= 20 and struct.unpack_from("<H", header, 18)[0] == machine:
                directories.append(directory)
                break
    return directories


def _shell_path(path: Path, root: Path) -> str:
    # Package paths are reviewed Debian archive members; quote their literal
    # suffix without interpolating a build-directory prefix into the output.
    return '"$root"/' + shlex.quote(str(path.relative_to(root)))


def _relocate_linker_scripts(root: Path, triple: str) -> None:
    # Match toolchains/sysroot/extensions.bzl: sandbox input symlinks can place
    # a script physically outside --sysroot. Explicit =/ inputs keep GNU ld
    # from resolving the script's libc/libm references against the build host.
    for name in ("libc.so", "libm.so", "libm.a"):
        script = root / "usr/lib" / triple / name
        data = script.read_bytes()
        # ARMHF has no vector-math companion archive: libm.a is a real archive
        # and libm.so is a shared-object alias, rather than GNU ld scripts.
        if data.startswith((b"!<arch>\n", b"\x7fELF")):
            continue
        original = data.decode()
        paths = [token for token in original.split()
                 if token.startswith(("/lib/", "/lib64/", "/usr/lib/"))]
        if not paths:
            raise ValueError("expected absolute library inputs in pinned linker script: " + name)
        updated = original
        for path in dict.fromkeys(paths):
            if not (root / path.lstrip("/")).is_file():
                raise ValueError("missing declared linker-script input: " + path)
            updated = updated.replace(path, "=" + path)
        script.write_text(updated)


def prepare_kernel(root: Path, architecture: str, cross_architectures: list[str] | None = None) -> None:
    """Add relocatable executable wrappers to an already extracted tool tree."""
    triple, machine = ARCHITECTURES[architecture]
    _relocate_linker_scripts(root, triple)
    cross_triples = []
    for target in cross_architectures or []:
        target_triple = {"arm64": "aarch64-linux-gnu", "armhf": "arm-linux-gnueabihf"}.get(target)
        if architecture != "amd64" or not target_triple:
            raise ValueError("kernel cross compilation requires AMD64 to ARM64 or ARMHF")
        _relocate_linker_scripts(root, target_triple)
        cross_triples.append(target_triple)
    loader = loader_path(root, architecture)
    libraries = _native_libraries(root, machine)
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": ""}
    prefix = [str(loader), "--inhibit-cache", "--library-path", os.pathsep.join(map(str, libraries))]

    # Preserve Perl's own module ordering while removing all built-in host
    # directories, including those reached by scripts through their shebang.
    perl = root / "usr/bin/perl"
    builtins = subprocess.check_output(prefix + [str(perl), "-e", 'print join("\\0", @INC)'], env=environment).decode().split("\0")
    perl_directories = [root / p.lstrip("/") for p in builtins if p.startswith("/") and (root / p.lstrip("/")).is_dir()]
    bootstrap = root / "share/sonic-build-tools/Sonic/KernelTools.pm"
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    quoted = ", ".join("'" + p.replace("'", "\\'") + "'" for p in builtins)
    bootstrap.write_text("""package Sonic::KernelTools;
BEGIN {
    my %host = map { $_ => 1 } (""" + quoted + """);
    @INC = grep { !$host{$_} } @INC;
    $^X = "$ENV{SONIC_BUILD_TOOLS_ROOT}/bin/perl";
}
1;
""")

    # The original /bin alias is only useful when this tree is used as a root
    # filesystem. Keep all package binaries in usr/bin and expose launchers in
    # bin instead. Libraries and headers retain their ordinary sysroot layout.
    commands = root / "bin"
    if commands.is_symlink():
        commands.unlink()
    commands.mkdir(exist_ok=True)
    native_library_path = ":".join('$root/' + str(p.relative_to(root)) for p in libraries)
    perl_path = ":".join('$root/' + str(p.relative_to(root)) for p in [bootstrap.parent.parent] + perl_directories)
    loader_expression = _shell_path(loader, root)

    def preamble(destination: Path) -> str:
        relative = os.path.relpath(root, destination.parent)
        return '''#!/bin/sh
# Generated from declared Debian tools; no host tool lookup is needed.
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/''' + relative + '''" && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
SONIC_BUILD_TOOLS_ROOT="$root"
PATH="$root/bin"
SHELL="$root/bin/sh"
CONFIG_SHELL="$root/bin/sh"
DPKG_DATADIR="$root/usr/share/dpkg"
DPKG_ORIGINS_DIR="$root/etc/dpkg/origins"
: "${DPKG_ADMINDIR:=$root/var/lib/dpkg}"
export SONIC_BUILD_TOOLS_ROOT PATH SHELL CONFIG_SHELL DPKG_DATADIR DPKG_ORIGINS_DIR DPKG_ADMINDIR
'''

    def elf_command(binary: Path) -> str:
        return 'exec ' + loader_expression + ' --inhibit-cache --library-path "' + native_library_path + '" ' + _shell_path(binary, root) + ' '

    tools: dict[str, Path] = {}
    for directory in (root / "usr/bin", root / "usr/sbin"):
        for binary in sorted(directory.iterdir()):
            if binary.is_file() and os.access(binary, os.X_OK):
                tools.setdefault(binary.name, binary)
    tools.update({"sh": root / "usr/bin/dash", "awk": root / "usr/bin/mawk", "cc": root / "usr/bin/gcc", "c++": root / "usr/bin/g++"})
    tools["host-gcc"] = tools["gcc"]
    tools["host-g++"] = tools["g++"]
    for target_triple in cross_triples:
        tools[target_triple + "-pkg-config"] = tools["pkgconf"]
    pkg_config_triples = {target_triple + "-pkg-config": target_triple for target_triple in cross_triples}
    pkg_config_triples.update({"pkgconf": triple, "pkg-config": triple})

    # GCC executes its compiler passes and binutils as child programs. Give
    # every pass the same loader/library contract rather than leaking LD_* to
    # the C programs that the compiler produces.
    compiler_directories: dict[str, str] = {}
    for layout in ("gcc", "gcc-cross"):
        for version in sorted((root / "usr/libexec" / layout).glob("*/*")):
            compiler_triple = version.parent.name
            compiler_directories[compiler_triple] = layout
            wrappers = root / "tools" / compiler_triple
            wrappers.mkdir(parents=True, exist_ok=True)
            for name in ("cc1", "cc1plus", "collect2", "lto1", "lto-wrapper"):
                binary = version / name
                if binary.is_file():
                    destination = wrappers / name
                    destination.write_text(preamble(destination) + elf_command(binary) + '"$@"\n')
                    destination.chmod(0o755)
            for name in ("ar", "as", "ld", "nm", "objcopy", "objdump", "ranlib", "readelf", "strip"):
                binary = root / "usr/bin" / (compiler_triple + "-" + name)
                if binary.is_file():
                    destination = wrappers / name
                    destination.write_text(preamble(destination) + elf_command(binary) + '"$@"\n')
                    destination.chmod(0o755)

    emitted = []
    for name, binary in tools.items():
        header = _header(binary)
        destination = commands / name
        script = preamble(destination)
        resolved_name = binary.resolve().name
        driver = re.fullmatch(r"(.+)-(gcc|g\+\+|cpp)(?:-[0-9]+)?", resolved_name)
        if name == "yacc":
            # Debian's bison.yacc shell script hard-codes /usr/bin/bison.
            # Keep yacc compatibility while routing through the declared ELF,
            # skeletons and m4 instead of the execution host's installation.
            script += 'exec "$root/bin/bison" -y "$@"\n'
        elif header[:4] == b"\x7fELF":
            if struct.unpack_from("<H", header, 18)[0] != machine:
                continue
            if name.startswith("python3"):
                script += 'PYTHONHOME="$root/usr"\nPYTHONNOUSERSITE=1\nPYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$root/usr/share/dh-python:$root/usr/share/python3"\nexport PYTHONHOME PYTHONNOUSERSITE PYTHONPATH\n'
            if name == "perl" or resolved_name.startswith("perl5"):
                script += 'PERL5LIB="' + perl_path + '"\nPERL5OPT="-MSonic::KernelTools"\nexport PERL5LIB PERL5OPT\n'
            if name in ("bison", "flex", "lex", "yacc"):
                script += 'M4="$root/bin/m4"\nBISON_PKGDATADIR="$root/usr/share/bison"\nexport M4 BISON_PKGDATADIR\n'
            if name == "make":
                script += 'MAKE="$root/bin/make"\nexport MAKE\n'
            if name == "file":
                script += 'MAGIC="$root/usr/share/misc/magic.mgc"\nexport MAGIC\n'
            if name in pkg_config_triples:
                pkg_triple = pkg_config_triples[name]
                script += 'PKG_CONFIG_SYSROOT_DIR="$root"\nPKG_CONFIG_LIBDIR="$root/usr/lib/' + pkg_triple + '/pkgconfig:$root/usr/share/pkgconfig"\nexport PKG_CONFIG_SYSROOT_DIR PKG_CONFIG_LIBDIR\n'
            if driver and driver[1] in compiler_directories:
                compiler_triple = driver[1]
                layout = compiler_directories[compiler_triple]
                script += 'GCC_EXEC_PREFIX="$root/usr/lib/' + layout + '/"\nCOMPILER_PATH="$root/tools/' + compiler_triple + '"\nexport GCC_EXEC_PREFIX COMPILER_PATH\n'
                script += elf_command(binary) + '-B "$root/tools/' + compiler_triple + '/" --sysroot="$root" '
                if compiler_triple in cross_triples:
                    # Preserve GCC's builtin header precedence for freestanding
                    # kernel sources, including ARMHF softfp NEON intrinsics.
                    script += '-B "$root/usr/lib/' + compiler_triple + '/" -L "$root/usr/lib/' + compiler_triple + '/" -idirafter "$root/usr/include/' + compiler_triple + '" -idirafter "$root/usr/include" '
                if name in ("host-gcc", "host-g++"):
                    script += '"-Wl,--dynamic-linker=$root/' + str(loader.relative_to(root)) + '" "-Wl,-rpath,' + native_library_path + '" '
            else:
                script += elf_command(binary)
            script += '"$@"\n'
        elif header.startswith(b"#!"):
            interpreter = shlex.split(header.splitlines()[0][2:].decode())
            command = Path(interpreter.pop(0)).name
            if command == "env" and interpreter:
                command = interpreter.pop(0)
            if command not in ("sh", "bash", "dash", "perl", "python3", "python3.13"):
                continue
            script += 'exec "$root/bin/' + command + '" ' + " ".join(shlex.quote(arg) for arg in interpreter) + ' ' + _shell_path(binary, root) + ' "$@"\n'
        else:
            continue
        destination.write_text(script)
        destination.chmod(0o755)
        emitted.append(name)

    required = {"sh", "bash", "perl", "python3", "gcc", "g++", "host-gcc", "make", "dpkg", "dpkg-query", "dpkg-source", "dpkg-buildpackage", "dh_testroot", "quilt", "kernel-wedge", "pahole", "kmod"}
    if architecture == "arm64":
        required |= {"arm-linux-gnueabihf-gcc", "arm-linux-gnueabihf-ld"}
    for target_triple in cross_triples:
        required |= {target_triple + "-gcc", target_triple + "-ld", target_triple + "-objcopy", target_triple + "-pkg-config"}
        if target_triple == "aarch64-linux-gnu":
            required |= {"arm-linux-gnueabihf-gcc", "arm-linux-gnueabihf-ld"}
    missing = required - set(emitted)
    if missing:
        raise RuntimeError("kernel tool runtime is missing launchers: " + ", ".join(sorted(missing)))
    prepare_debian(root)
    (root / "kernel-tools.json").write_text(json.dumps({
        "schema": 1, "architecture": architecture, "execution": "relocatable",
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(p.relative_to(root)) for p in libraries],
        "tools": sorted(emitted),
    }, indent=2, sort_keys=True) + "\n")
