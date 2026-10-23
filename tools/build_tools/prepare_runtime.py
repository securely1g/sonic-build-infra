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
            # Later archive entries may have replaced a queued link's parent
            # with a symlink. Recheck both paths at the moment we create it.
            target = safe_path(root, str(target.relative_to(root)))
            source = safe_path(root, name)
            if not source.resolve().is_relative_to(root):
                raise ValueError(f"hardlink source escapes output: {name}")
            if not source.is_file():
                raise ValueError(f"missing hardlink target: {name}")
            os.link(source.resolve(), target)


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


def prepare_configure(root: Path, architecture: str, loader: Path, libraries: list[Path], *, foreign: bool = False) -> None:
    """Keep foreign_cc tools self-contained when it copies their tree artifact."""
    script_tools = ("autoconf", "autoheader", "autom4te")
    tools = {name: root / "usr/bin" / name for name in
             ("make", "m4", "pkgconf", "perl", "bison", "flex", "grep", *script_tools)}
    shell_tools = ()
    if foreign:
        script_tools += ("autoreconf", "automake", "aclocal")
        shell_tools = ("libtoolize", "which")
        for name in ("autoreconf", "libtoolize", "protoc", "grpc_cpp_plugin", "grpc_python_plugin",
                     "thrift", "python3", "dot", "bash", "dash", "sed", "mawk", "find", "xargs",
                     "basename", "cat", "chmod", "cp", "cut", "dirname", "echo", "expr", "head", "install",
                     "ln", "mkdir", "mv", "od", "printf", "readlink", "realpath", "rm", "rmdir",
                     "sleep", "sort", "tail", "test", "touch", "tr", "uname", "wc", "cmp", "diff"):
            tools[name] = root / "usr/bin" / name
        for name in ("automake", "aclocal"):
            candidates = sorted((root / "usr/bin").glob(name + "-[0-9]*"))
            if len(candidates) != 1:
                raise RuntimeError(f"expected one declared {name} version")
            tools[name] = candidates[0]
        libtoolize = tools["libtoolize"]
        text = libtoolize.read_text()
        for directory in ("/usr/share/libtool/build-aux", "/usr/share/libtool", "/usr/share/aclocal"):
            assignment = '="' + directory + '"'
            if text.count(assignment) != 1:
                raise RuntimeError("unexpected libtoolize data-directory configuration")
            text = text.replace(assignment, '="$SONIC_BUILD_TOOLS_ROOT' + directory + '"')
        libtoolize.write_text(text)
        tools["which"] = root / "usr/bin/which.debianutils"
        tools["awk"] = tools["mawk"]
        tools["sh"] = tools["dash"]
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
    plugin_files = list(root.glob("usr/lib/*/graphviz/*.so*")) if foreign else []
    for binary in [path for name, path in tools.items() if name not in script_tools + shell_tools] + list(root.glob("usr/lib/*/perl*/*/auto/**/*.so")) + plugin_files:
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
    if foreign:
        automake_modules = sorted((root / "usr/share").glob("automake-[0-9]*"))
        if len(automake_modules) != 1:
            raise RuntimeError("expected one declared Automake module directory")
        perl_directories.append(automake_modules[0])
        # protoc normally discovers well-known schemas relative to its binary.
        # /proc/self/exe instead names our explicit private loader, so the
        # wrapper must append the matching package's schema directory.
        for schema in ("any.proto", "empty.proto", "descriptor.proto"):
            if not (root / "usr/include/google/protobuf" / schema).is_file():
                raise RuntimeError("protoc packages contain no " + schema)
    perl_path = ":".join('$root/' + str(path.relative_to(root)) for path in [bootstrap.parent.parent] + perl_directories)
    if foreign:
        plugin_directories = sorted({path.parent for path in plugin_files})
        if len(plugin_directories) != 1:
            raise RuntimeError("expected one declared Graphviz plugin directory")
        fontconfig = root / "etc/fonts/sonic-build-tools.conf"
        fontconfig.parent.mkdir(parents=True, exist_ok=True)
        fontconfig.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig><dir prefix="relative">../../usr/share/fonts</dir><cachedir prefix="xdg">sonic-build-tools/fontconfig</cachedir></fontconfig>\n')
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
        if foreign:
            script += 'PATH="$root/bin${PATH:+:$PATH}"\nexport PATH\n'
            script += 'AUTOMAKE_LIBDIR="$root/' + str(automake_modules[0].relative_to(root)) + '"\n'
            script += 'ACLOCAL_PATH="$root/usr/share/aclocal"\nACLOCAL_AUTOMAKE_DIR="$root/usr/share/aclocal-' + automake_modules[0].name.removeprefix("automake-") + '"\nAUTOMAKE_UNINSTALLED=1\nSONIC_BUILD_TOOLS_ROOT="$root"\n'
            script += 'AUTOCONF="$root/bin/autoconf"\nAUTOHEADER="$root/bin/autoheader"\nAUTOMAKE="$root/bin/automake"\nACLOCAL="$root/bin/aclocal"\nLIBTOOLIZE="$root/bin/libtoolize"\n'
            script += 'GREP="$root/bin/grep"\nEGREP="$root/bin/grep -E"\nFGREP="$root/bin/grep -F"\nSED="$root/bin/sed"\n'
            script += 'export AUTOMAKE_UNINSTALLED SONIC_BUILD_TOOLS_ROOT AUTOMAKE_LIBDIR ACLOCAL_PATH ACLOCAL_AUTOMAKE_DIR AUTOCONF AUTOHEADER AUTOMAKE ACLOCAL LIBTOOLIZE GREP EGREP FGREP SED\n'
        if name == "dot":
            script += 'GVBINDIR="$root/' + str(plugin_directories[0].relative_to(root)) + '"\nFONTCONFIG_FILE="$root/etc/fonts/sonic-build-tools.conf"\nXDG_CACHE_HOME="${TEST_TMPDIR:-${TMPDIR:-/tmp}}"\nexport GVBINDIR FONTCONFIG_FILE XDG_CACHE_HOME\n'
        if name == "python3":
            script += 'PYTHONHOME="$root/usr"\nPYTHONNOUSERSITE=1\nexport PYTHONHOME PYTHONNOUSERSITE\n'
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
        if name == "protoc":
            script += '''# Keep explicit include precedence and protoc's implicit current directory.
has_proto_include() {
    for argument do
        case "$argument" in
            -I*|--proto_path|--proto_path=*) return 0 ;;
            @*)
                # protoc response files contain one argument per line.
                if [ -f "${argument#@}" ]; then
                    while IFS= read -r response || [ -n "$response" ]; do
                        case "$response" in -I*|--proto_path|--proto_path=*) return 0 ;; esac
                    done < "${argument#@}"
                fi ;;
        esac
    done
    return 1
}
if has_proto_include "$@"; then
    set -- "$@" "--proto_path=$root/usr/include"
else
    set -- "$@" -I. "--proto_path=$root/usr/include"
fi
'''
        if name in script_tools:
            script += 'exec "$root/bin/perl" "$root/' + str(binary.relative_to(root)) + '" '
            if name == "autom4te":
                script += '--prepend-include "$root/usr/share/autoconf" '
            if name == "aclocal":
                script += '--system-acdir "$root/usr/share/aclocal" '
            script += '"$@"\n'
        elif name in shell_tools:
            script += 'pkgdatadir="$root/usr/share/libtool"\naclocaldir="$root/usr/share/aclocal"\nexport pkgdatadir aclocaldir\n'
            script += 'exec "$root/bin/sh" "$root/' + str(binary.relative_to(root)) + '" "$@"\n'
        else:
            script += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" "$root/' + str(binary.relative_to(root)) + '" "$@"\n'
        launcher = root / "bin" / name
        # A copied merged-/usr alias (for example sh -> ../usr/bin/dash)
        # still points at the packaged executable. Replace the alias itself;
        # writing through it would corrupt the executable our wrapper invokes.
        launcher.unlink(missing_ok=True)
        launcher.write_text(script)
        launcher.chmod(0o755)
    versions = {name: run_tool([str(root / "bin" / name), "--version"], environment=environment).decode().strip()
                for name in ("make", "m4", "pkgconf", "perl", "bison", "flex", "autoconf", "autoheader", "autom4te") +
                (("autoreconf", "automake", "aclocal", "libtoolize", "protoc", "python3") if foreign else ())}
    if foreign:
        # Debian generates this plugin index in postinst. Reproduce it with the
        # declared executable, and keep relative plugin names for relocation.
        run_tool([str(root / "bin/dot"), "-c"], environment=environment)
        indexes = list(plugin_directories[0].glob("config*"))
        if len(indexes) != 1 or str(root) in indexes[0].read_text():
            raise RuntimeError("Graphviz generated no relocatable plugin index")
        os.utime(indexes[0], (0, 0))
    manifest = {
        "format_version": 1,
        "profile": "foreign" if foreign else "configure",
        "architecture": architecture,
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": {name: str(path.relative_to(root)) for name, path in tools.items()},
        "perl_directories": [str(path.relative_to(root)) for path in perl_directories],
        "versions": versions,
    }
    if foreign:
        # Optional Debian alternatives (for example /etc/rmt) can point at
        # packages outside the selected closure. They are not executable
        # inputs, and dangling links are invalid inside Bazel tree outputs.
        dangling = [path for path in root.rglob("*") if path.is_symlink() and not path.exists()]
        manifest["omitted_unresolved_symlinks"] = sorted(str(path.relative_to(root)) for path in dangling)
        for path in dangling:
            path.unlink()
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


def prepare_kerberos(root: Path, architecture: str, loader: Path, libraries: list[Path]) -> None:
    """Relocate Debian's error/command table generators and their private tools."""
    scripts = {"compile_et": "et", "mk_cmds": "ss"}
    binaries = ("mawk", "sed", "grep", "basename", "cmp", "rm", "mv", "chmod")
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": ""}
    prefix = [str(loader), "--library-path", os.pathsep.join(map(str, libraries))]
    for name in binaries:
        binary = root / "usr/bin" / name
        if not binary.is_file():
            raise RuntimeError(f"Kerberos tool package contains no {name}")
        listing = run_tool(prefix + ["--list", str(binary)], environment=environment).decode()
        if "not found" in listing:
            raise RuntimeError(f"incomplete runtime for {name}:\n{listing}")
        for resolved in re.findall(r"=>\s+(/\S+)", listing):
            if not Path(resolved).resolve().is_relative_to(root):
                raise RuntimeError(f"{name} resolved an undeclared library: {resolved}")
    for name, directory in scripts.items():
        script = root / "usr/bin" / name
        text = script.read_text()
        if text.count("AWK=/usr/bin/awk\n") != 1:
            raise RuntimeError(f"Unexpected AWK configuration in {name}")
        # Preserve Debian's generator logic. Its template directory has an
        # upstream override; only the hardcoded interpreter needs relocation.
        text = text.replace("AWK=/usr/bin/awk\n", 'AWK="$SONIC_BUILD_TOOLS_AWK"\n')
        script.write_text(text)
        required = ("et_h.awk", "et_c.awk") if directory == "et" else ("ct_c.sed", "ct_c.awk")
        if not all((root / "usr/share" / directory / file).is_file() for file in required):
            raise RuntimeError(f"Missing declared {name} templates")
    (root / "bin").mkdir(exist_ok=True)
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    for name in (*binaries, *scripts):
        launcher = '''#!/bin/sh
# AUTO-GENERATED. DO NOT EDIT MANUALLY.
# Generator: tools/build_tools/prepare_runtime.py
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
'''
        if name in scripts:
            variable = "_ET_DIR_OVERRIDE" if name == "compile_et" else "_SS_DIR_OVERRIDE"
            launcher += 'PATH="$root/bin"\nSONIC_BUILD_TOOLS_AWK="$root/bin/mawk"\n'
            launcher += variable + '="$root/usr/share/' + scripts[name] + '"\n'
            launcher += 'export PATH SONIC_BUILD_TOOLS_AWK ' + variable + '\n'
            launcher += 'exec /bin/sh "$root/usr/bin/' + name + '" "$@"\n'
        else:
            launcher += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" "$root/usr/bin/' + name + '" "$@"\n'
        path = root / "bin" / name
        path.write_text(launcher)
        path.chmod(0o755)
    manifest = {
        "_generated": {"notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.", "generator": "tools/build_tools/prepare_runtime.py"},
        "format_version": 1,
        "profile": "kerberos",
        "architecture": architecture,
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": {name: "usr/bin/" + name for name in (*binaries, *scripts)},
    }
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


def materialize_directory_aliases(root: Path, *, preserve_root_aliases: bool = False) -> None:
    """Keep cached tree children independent of directory-symlink restore order.

    File symlinks keep pointing to their original declared targets, including
    when an alias copies a directory to a different depth in the runtime.
    Kernel tools retain their intentional root-level merged-/usr entrypoints.
    """
    root = root.resolve()
    while True:
        aliases = []
        for path in sorted(root.rglob("*")):
            if not path.is_symlink() or not path.is_dir():
                continue
            if (preserve_root_aliases and path.parent == root
                    and path.name in ("bin", "sbin", "lib", "lib32", "lib64", "libx32")
                    and path.readlink() == Path("usr") / path.name):
                continue
            aliases.append(path)
        if not aliases:
            return
        for path in aliases:
            if not path.is_symlink():
                continue
            target = path.resolve(strict=True)
            if not target.is_relative_to(root) or path.is_relative_to(target):
                raise ValueError(f"invalid runtime directory alias: {path.relative_to(root)}")
            # Copying literal relative links can change their destination when
            # the directory alias and its target have different parents.
            links = []
            for source in target.rglob("*"):
                if not source.is_symlink():
                    continue
                destination = source.resolve(strict=False)
                if not destination.is_relative_to(root):
                    raise ValueError(f"runtime symlink escapes output: {source.relative_to(root)}")
                links.append((source.relative_to(target), destination))
            path.unlink()
            shutil.copytree(target, path, symlinks=True)
            for relative, destination in links:
                copied = path / relative
                copied.unlink()
                copied.symlink_to(os.path.relpath(destination, copied.parent))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--tar", action="append", default=[])
    parser.add_argument("--profile", choices=("documentation", "configure", "kerberos", "foreign"), default="documentation")
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for payload in sorted(map(Path, args.tar)):
        extract_payload(root, payload)

    if args.profile in ("configure", "foreign"):
        # X11's unused compatibility alias points back to its own parent.
        if args.profile == "foreign":
            for path in (root / "usr/bin/X11", root / "bin/X11"):
                if path.is_symlink() and path.resolve() == path.parent:
                    path.unlink()
        materialize_directory_aliases(root)
    loader = loader_path(root, args.architecture)
    libraries = library_directories(root)
    prefix = [str(loader), "--library-path", os.pathsep.join(map(str, libraries))]
    if args.profile in ("configure", "foreign"):
        prepare_configure(root, args.architecture, loader, libraries, foreign=args.profile == "foreign")
        return
    if args.profile == "kerberos":
        prepare_kerberos(root, args.architecture, loader, libraries)
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
    (root / "bin").mkdir(exist_ok=True)
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    for name in ("doxygen", "aspell"):
        script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
'''
        script += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" "$root/' + str(tools[name].relative_to(root)) + '" '
        if name == "aspell":
            script += '--lang=en --data-dir="$root/' + str(data_dir.relative_to(root)) + '" --dict-dir="$root/' + str(dictionary_dir.relative_to(root)) + '" --add-filter-path="$root/' + str(filter_dir.relative_to(root)) + '" '
        script += '"$@"\n'
        wrapper = root / "bin" / name
        wrapper.write_text(script)
        wrapper.chmod(0o755)
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
