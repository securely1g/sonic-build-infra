"""Prepare shared build tools from their pinned Debian package payloads."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile


def safe_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name.removeprefix("./"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"invalid archive path: {name}")
    target = root.joinpath(*relative.parts)
    if not target.parent.resolve().is_relative_to(root):
        raise ValueError(f"archive path escapes output: {name}")
    return target


def extract_payload(root: Path, payload: Path) -> None:
    with tarfile.open(payload, mode="r:*") as archive:
        hardlinks = []
        for member in archive:
            if member.name in (".", "./"):
                continue
            target = safe_path(root, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            if member.issym():
                link = member.linkname
                if link.startswith("/"):
                    link = os.path.relpath(root / link.lstrip("/"), target.parent)
                target.symlink_to(link)
            elif member.islnk():
                hardlinks.append((target, member.linkname))
            elif member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"missing archive data: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
                os.utime(target, (0, 0))
        for target, name in hardlinks:
            source = safe_path(root, name)
            if not source.is_file():
                raise ValueError(f"missing hardlink target: {name}")
            os.link(source, target)


def loader_path(root: Path, architecture: str) -> Path:
    names = {
        "amd64": ("x86_64-linux-gnu", "ld-linux-x86-64.so.2"),
        "arm64": ("aarch64-linux-gnu", "ld-linux-aarch64.so.1"),
    }
    triplet, loader = names[architecture]
    candidates = [
        root / "usr/lib" / triplet / loader,
        root / "lib" / triplet / loader,
        root / "usr/lib64" / loader,
        root / "lib64" / loader,
        root / "lib" / loader,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"build_tools {architecture} packages contain no dynamic loader")


def library_directories(root: Path) -> list[Path]:
    directories = set()
    for path in root.rglob("*.so*"):
        if path.is_file():
            directories.add(path.parent)
    return sorted(directories)


def run_tool(command: list[str], *, environment: dict[str, str], data: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=data, env=environment, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"tool exited {result.returncode}: {command}\n{result.stderr.decode(errors='replace')}")
    return result.stdout


def prepare_configure(root: Path, architecture: str, loader: Path, libraries: list[Path]) -> None:
    """Keep foreign_cc tools self-contained when it copies their tree artifact."""
    script_tools = ("autoconf", "autoheader", "autom4te")
    tools = {name: root / "usr/bin" / name for name in
             ("make", "m4", "pkgconf", "perl", "bison", "flex", "grep", *script_tools)}
    for name, path in tools.items():
        if not path.is_file():
            raise RuntimeError(f"configure packages contain no {name}")
    # Replace the package's compiled-in macro-directory options with one
    # launcher-provided path. Keep every language/macro definition unchanged.
    config = root / "usr/share/autoconf/autom4te.cfg"
    lines = config.read_text().splitlines(keepends=True)
    directory_option = "args: --prepend-include '/usr/share/autoconf'"
    if sum(line.strip() == directory_option for line in lines) != 4:
        raise RuntimeError("unexpected Autoconf macro-directory configuration")
    config.write_text("".join(line for line in lines if line.strip() != directory_option))
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": ""}
    prefix = [str(loader), "--library-path", os.pathsep.join(map(str, libraries))]
    for binary in [path for name, path in tools.items() if name not in script_tools] + list(root.glob("usr/lib/*/perl*/*/auto/**/*.so")):
        if not binary.is_file():
            raise RuntimeError(f"configure packages contain no tool: {binary}")
        listing = run_tool(prefix + ["--list", str(binary)], environment=environment).decode()
        if "not found" in listing:
            raise RuntimeError(f"incomplete runtime for {binary}:\n{listing}")
        for resolved in re.findall(r"=>\s+(/\S+)", listing):
            if not Path(resolved).resolve().is_relative_to(root):
                raise RuntimeError(f"{binary.name} resolved an undeclared library: {resolved}")

    # Perl embeds system library directories in its interpreter. Preserve the
    # caller's explicit -I inputs, but remove those built-in host directories.
    # The relocated core and XS modules come from the declared package bundle.
    builtins = run_tool(prefix + [str(tools["perl"]), "-e", 'print join("\\0", @INC)'], environment=environment).decode().split("\0")
    # Preserve Perl's configured search order. Searching for arbitrary files
    # named Config.pm also finds nested packages such as Encode and Net; adding
    # those directories would make "use Symbol" load Encode/Symbol.pm.
    perl_directories = [root / path.lstrip("/") for path in builtins
                        if path.startswith("/") and (root / path.lstrip("/")).is_dir()]
    for module in ("Config.pm", "strict.pm", "DynaLoader.pm"):
        if not any((directory / module).is_file() for directory in perl_directories):
            raise RuntimeError(f"configure packages contain no Perl core module: {module}")
    bootstrap = root / "share/sonic-build-tools/Sonic/BuildTools.pm"
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    quoted = ", ".join("'" + path.replace("'", "\\'") + "'" for path in builtins)
    bootstrap.write_text("""package Sonic::BuildTools;
BEGIN {
    my %host = map { $_ => 1 } (""" + quoted + """);
    @INC = grep { !$host{$_} } @INC;
    # /proc/self/exe names ld.so because the launcher starts the private loader.
    # Configure scripts use $^X when generating later Perl invocations.
    my $root = __FILE__;
    $root =~ s{/share/sonic-build-tools/Sonic/BuildTools.pm$}{};
    $^X = "$root/bin/perl";
}
1;
""")
    (root / "bin").mkdir(exist_ok=True)
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    perl_path = ":".join('$root/' + str(path.relative_to(root)) for path in [bootstrap.parent.parent] + perl_directories)
    for name, binary in tools.items():
        script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
'''
        if name in script_tools:
            script += '''PATH="$root/bin${PATH:+:$PATH}"
export PATH
autom4te_perllibdir="$root/usr/share/autoconf"
AC_MACRODIR="$root/usr/share/autoconf"
AUTOM4TE_CFG="$root/usr/share/autoconf/autom4te.cfg"
trailer_m4="$root/usr/share/autoconf/autoconf/trailer.m4"
AUTOM4TE="$root/bin/autom4te"
M4="$root/bin/m4"
export autom4te_perllibdir AC_MACRODIR AUTOM4TE_CFG trailer_m4 AUTOM4TE M4
'''
        if name in ("bison", "flex"):
            script += 'M4="$root/bin/m4"\nBISON_PKGDATADIR="$root/usr/share/bison"\nexport M4 BISON_PKGDATADIR\n'
        if name == "make":
            script += 'MAKE="$root/bin/make"\nexport MAKE\n'
        if name == "perl":
            script += 'PERL5LIB="' + perl_path + '"\nPERL5OPT="-MSonic::BuildTools"\nexport PERL5LIB PERL5OPT\n'
        if name == "pkgconf":
            # No host /usr/lib/pkgconfig fallback; foreign_cc supplies explicit
            # PKG_CONFIG_LIBDIR/PATH for its declared target dependencies.
            script += ': "${PKG_CONFIG_LIBDIR:=}"\nexport PKG_CONFIG_LIBDIR\n'
        if name in script_tools:
            script += 'exec "$root/bin/perl" "$root/' + str(binary.relative_to(root)) + '" '
            if name == "autom4te":
                script += '--prepend-include "$root/usr/share/autoconf" '
            script += '"$@"\n'
        else:
            script += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" "$root/' + str(binary.relative_to(root)) + '" "$@"\n'
        launcher = root / "bin" / name
        launcher.write_text(script)
        launcher.chmod(0o755)
    versions = {name: run_tool([str(root / "bin" / name), "--version"], environment=environment).decode().strip() for name in tools}
    manifest = {
        "format_version": 1,
        "profile": "configure",
        "architecture": architecture,
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": {name: str(path.relative_to(root)) for name, path in tools.items()},
        "perl_directories": [str(path.relative_to(root)) for path in perl_directories],
        "versions": versions,
    }
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--tar", action="append", default=[])
    parser.add_argument("--profile", choices=("documentation", "configure"), default="documentation")
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for payload in sorted(map(Path, args.tar)):
        extract_payload(root, payload)

    if args.profile == "configure":
        # Bazel caches files inside tree outputs. Materialize directory aliases
        # (notably Perl's 5.40 -> 5.40.1) so cache restoration never has to create
        # a child through a directory symlink before its target is populated.
        while True:
            links = [path for path in root.rglob("*") if path.is_symlink() and path.is_dir()]
            if not links:
                break
            for path in links:
                target = path.resolve(strict=True)
                if not target.is_relative_to(root) or path.is_relative_to(target):
                    raise ValueError(f"invalid runtime directory alias: {path}")
                path.unlink()
                shutil.copytree(target, path, symlinks=True)
    loader = loader_path(root, args.architecture)
    libraries = library_directories(root)
    prefix = [str(loader), "--library-path", os.pathsep.join(map(str, libraries))]
    if args.profile == "configure":
        prepare_configure(root, args.architecture, loader, libraries)
        return
    tools = {name: root / path for name, path in {
        "aspell": "usr/bin/aspell",
        "doxygen": "usr/bin/doxygen",
        "prezip_bin": "usr/bin/prezip-bin",
    }.items()}
    filter_candidates = sorted(root.rglob("nroff.amf"))
    if len(filter_candidates) != 1:
        raise RuntimeError("build_tools packages must contain one Aspell filter directory")
    filter_dir = filter_candidates[0].parent
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": "", "HOME": str(root / ".home")}
    (root / ".home").mkdir()
    runtime_files = list(tools.items()) + [(path.name, path) for path in sorted(filter_dir.glob("*.so"))]
    for name, binary in runtime_files:
        if not binary.is_file():
            raise RuntimeError(f"build_tools packages contain no {name}: {binary}")
        listing = run_tool(prefix + ["--list", str(binary)], environment=environment).decode()
        if "not found" in listing:
            raise RuntimeError(f"incomplete runtime for {name}:\n{listing}")
        for resolved in re.findall(r"=>\s+(/\S+)", listing):
            if not Path(resolved).resolve().is_relative_to(root):
                raise RuntimeError(f"{name} resolved an undeclared library: {resolved}")

    data_candidates = sorted(root.rglob("en.dat"))
    if not data_candidates:
        raise RuntimeError("build_tools packages contain no Aspell English language data")
    data_dir = data_candidates[0].parent
    dictionary_dir = data_dir
    aspell = prefix + [str(tools["aspell"]), "--lang=en", f"--data-dir={data_dir}", f"--dict-dir={dictionary_dir}", f"--add-filter-path={filter_dir}"]
    # Debian compiles compressed word lists in postinst. Reproduce that work
    # here using only the declared prezip-bin and Aspell executables.
    for wordlist in sorted(root.rglob("*.cwl.gz")):
        compressed = gzip.decompress(wordlist.read_bytes())
        words = run_tool(prefix + [str(tools["prezip_bin"]), "-d"], environment=environment, data=compressed)
        output = dictionary_dir / wordlist.name.removesuffix(".cwl.gz")
        output = output.with_name(output.name + ".rws")
        run_tool(aspell + ["--encoding=utf-8", "create", "master", str(output)], environment=environment, data=words)
        os.utime(output, (0, 0))

    run_tool(aspell + ["list"], environment=environment, data=b"switch\n")
    versions = {
        "doxygen": run_tool(prefix + [str(tools["doxygen"]), "-v"], environment=environment).decode().strip(),
        "aspell": run_tool(prefix + [str(tools["aspell"]), "--version"], environment=environment).decode().strip(),
    }
    shutil.rmtree(root / ".home")
    manifest = {
        "format_version": 1,
        "architecture": args.architecture,
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": {name: str(path.relative_to(root)) for name, path in tools.items()},
        "aspell_data_directory": str(data_dir.relative_to(root)),
        "aspell_dictionary_directory": str(dictionary_dir.relative_to(root)),
        "aspell_filter_directory": str(filter_dir.relative_to(root)),
        "versions": versions,
    }
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
