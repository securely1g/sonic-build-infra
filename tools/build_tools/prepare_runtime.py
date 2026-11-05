"""Prepare shared build tools from their pinned Debian package payloads."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
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


def validate_launcher(executable: Path, architecture: str) -> None:
    """Reject a host-dynamic or wrong-architecture bootstrap executable."""
    data = executable.read_bytes()
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("runtime launcher must be a little-endian ELF64 executable")
    if struct.unpack_from("<H", data, 18)[0] != {"amd64": 62, "arm64": 183}[architecture]:
        raise ValueError("runtime launcher architecture does not match the tool runtime")
    offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size, count = struct.unpack_from("<HH", data, 54)
    if entry_size < 4 or offset + entry_size * count > len(data):
        raise ValueError("invalid runtime launcher program headers")
    if any(struct.unpack_from("<I", data, offset + index * entry_size)[0] == 3 for index in range(count)):
        raise ValueError("runtime launcher must not require a host dynamic interpreter")


def write_launcher(root: Path, name: str, script: str, executable: Path) -> None:
    # The static executable resolves its own actual location even when a caller
    # exposes the tool through a differently named or relative file symlink.
    wrapper = root / "bin" / ("." + name + ".sh")
    wrapper.write_text(script)
    wrapper.chmod(0o755)
    destination = root / "bin" / name
    # Package aliases such as python3 -> python3.13 must not redirect the
    # copy: the executable's actual basename selects its adjacent sidecar.
    if destination.is_symlink() or destination.exists():
        destination.unlink()
    shutil.copyfile(executable, destination)
    destination.chmod(0o755)


def prepare_configure(root: Path, architecture: str, loader: Path, libraries: list[Path], executable: Path, *, foreign: bool = False, perl_only: bool = False, docbook: bool = False) -> None:
    """Keep foreign_cc tools self-contained when it copies their tree artifact."""
    script_tools = ("autoconf", "autoheader", "autom4te")
    tools = {name: root / "usr/bin" / name for name in
             ("make", "m4", "pkgconf", "perl", "bison", "flex", "grep", *script_tools)}
    shell_tools = ()
    if perl_only:
        script_tools = ()
        tools = {"perl": root / "usr/bin/perl"}
    if docbook:
        script_tools += ("sgmlspl",)
        for name in ("onsgmls", "sgmlspl", "cat", "date", "mktemp", "rm"):
            tools[name] = root / "usr/bin" / name
        tools["sh"] = root / "usr/bin/dash"
    if foreign:
        script_tools += ("autoreconf", "automake", "aclocal", "makeinfo")
        shell_tools = ("libtoolize", "which")
        for name in ("autoreconf", "libtoolize", "protoc", "grpc_cpp_plugin", "grpc_python_plugin",
                     "thrift", "python3", "sphinx-build", "makeinfo", "dot", "getconf", "bash", "dash", "sed", "mawk", "find", "xargs",
                     "basename", "cat", "chmod", "cp", "cut", "dirname", "echo", "expr", "head", "install",
                     "ln", "mkdir", "mv", "od", "printf", "readlink", "realpath", "rm", "rmdir",
                     "sleep", "sort", "tail", "test", "touch", "tr", "uname", "wc", "cmp", "diff",
                     "patch", "xmllint", "xsltproc"):
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
        # Texinfo's fallback relocation depends on whether the host happens to
        # have /usr/share/texinfo. Bind its installed data and XS directories to
        # the same declared runtime unconditionally.
        makeinfo = tools["makeinfo"].resolve()
        text = makeinfo.read_text()
        for original, relocated in [
            ("\n  my $datadir = '/usr/share';\n", '\n  my $datadir = "$ENV{SONIC_BUILD_TOOLS_ROOT}/usr/share";\n'),
            ("\n  my $xsdir = '/usr/lib/texinfo';\n", '\n  my $xsdir = "$ENV{SONIC_BUILD_TOOLS_ROOT}/usr/lib/texinfo";\n'),
        ]:
            if text.count(original) != 1:
                raise RuntimeError("unexpected Texinfo installed directory configuration")
            text = text.replace(original, relocated)
        for directory in ("/usr/share", "/usr/etc"):
            text = text.replace("eval '\"" + directory + "\"'", "eval '\"$ENV{SONIC_BUILD_TOOLS_ROOT}" + directory + "\"'")
        makeinfo.write_text(text)
        # Debian splits documentation templates into /usr/share packages.
        # Module-relative upstream lookups are replaced by those absolute
        # prefixes, so relocate the two package data entry points as well.
        for relative, original, replacement in [
            ("docutils/__init__.py", "datadir = os.path.join('/usr/share/docutils/', suffix)",
             "datadir = os.path.join(os.environ['SONIC_BUILD_TOOLS_ROOT'], 'usr/share/docutils', suffix)"),
            ("sphinx/__init__.py", "package_dir = '/usr/share/sphinx'",
             "package_dir = os.path.join(os.environ['SONIC_BUILD_TOOLS_ROOT'], 'usr/share/sphinx')"),
        ]:
            module = root / "usr/lib/python3/dist-packages" / relative
            content = module.read_text()
            if content.count(original) != 1:
                raise RuntimeError("unexpected Debian documentation data path: " + relative)
            module.write_text(content.replace(original, replacement))
        tools["which"] = root / "usr/bin/which.debianutils"
        tools["awk"] = tools["mawk"]
        tools["sh"] = tools["dash"]
    for name, path in tools.items():
        if not path.is_file():
            raise RuntimeError(f"configure packages contain no {name}")
    if not perl_only:
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
    for binary in [path for name, path in tools.items() if name not in script_tools + shell_tools + ("sphinx-build",)] + list(root.glob("usr/lib/*/perl*/*/auto/**/*.so")) + plugin_files:
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
            script += 'PERL5LIB="' + perl_path + '"\ncase " ${PERL5OPT:-} " in *" -MSonic::BuildTools "*) ;; *) PERL5OPT="-MSonic::BuildTools${PERL5OPT:+ $PERL5OPT}" ;; esac\nexport PERL5LIB PERL5OPT\n'
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
            script += 'exec "$root/bin/perl" '
            if name == "sgmlspl":
                script += '-I "$root/usr/share/perl5/sgmlspl-specs" '
            script += '"$root/' + str(binary.relative_to(root)) + '" '
            if name == "autom4te":
                script += '--prepend-include "$root/usr/share/autoconf" '
            if name == "aclocal":
                script += '--system-acdir "$root/usr/share/aclocal" '
            script += '"$@"\n'
        elif name == "sphinx-build":
            script += 'PYTHONPATH="$root/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"\nexport PYTHONPATH\n'
            script += 'exec "$root/bin/python3" "$root/' + str(binary.relative_to(root)) + '" "$@"\n'
        elif name in shell_tools:
            script += 'pkgdatadir="$root/usr/share/libtool"\naclocaldir="$root/usr/share/aclocal"\nexport pkgdatadir aclocaldir\n'
            script += 'exec "$root/bin/sh" "$root/' + str(binary.relative_to(root)) + '" "$@"\n'
        else:
            argv0 = ' --argv0 "$root/bin/python3"' if name == "python3" else ""
            if name in ("xmllint", "xsltproc"):
                # A caller supplies schemas/stylesheets explicitly. Never consult
                # the worker's mutable /etc/xml/catalog as a hidden build input.
                script += 'XML_CATALOG_FILES=\nexport XML_CATALOG_FILES\n'
            script += 'exec "$root/' + str(loader.relative_to(root)) + '"' + argv0 + ' --library-path "' + library_path + '" "$root/' + str(binary.relative_to(root)) + '" "$@"\n'
        write_launcher(root, name, script, executable)
    versions = {name: run_tool([str(root / "bin" / name), "--version"], environment=environment).decode().strip()
                for name in (("perl",) if perl_only else ("make", "m4", "pkgconf", "perl", "bison", "flex", "autoconf", "autoheader", "autom4te")) +
                (("autoreconf", "automake", "aclocal", "libtoolize", "protoc", "python3", "sphinx-build", "makeinfo", "getconf") if foreign else ())}
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
        "profile": "perl" if perl_only else "docbook" if docbook else "foreign" if foreign else "configure",
        "architecture": architecture,
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": {name: str(path.relative_to(root)) for name, path in tools.items()},
        "perl_directories": [str(path.relative_to(root)) for path in perl_directories],
        "versions": versions,
    }
    if foreign or perl_only:
        # Optional Debian alternatives (for example /etc/rmt) can point at
        # packages outside the selected closure. They are not executable
        # inputs, and dangling links are invalid inside Bazel tree outputs.
        dangling = [path for path in root.rglob("*") if path.is_symlink() and not path.exists()]
        manifest["omitted_unresolved_symlinks"] = sorted(str(path.relative_to(root)) for path in dangling)
        for path in dangling:
            path.unlink()
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if docbook:
        prepare_docbook(root, executable)


def prepare_docbook(root: Path, executable: Path) -> None:
    """Run Debian's original OpenSP/SGMLS man backend with declared data."""
    specification = root / "usr/share/perl5/sgmlspl-specs/docbook2man-spec.pl"
    backend = root / "usr/share/docbook-utils/backends/man"
    if not specification.is_file() or not backend.is_file():
        raise RuntimeError("missing declared DocBook man backend")
    # This is exactly the converter used by docbook-utils' man backend. The
    # jw frontend additionally searches for host Jade, paper size and catalogs,
    # although its man backend uses none of Jade's transformation routines.
    original = backend.read_text()
    for required in ("onsgmls $SGML_FILE", "sgmlspl $HELPER", "grep unresolved"):
        if required not in original:
            raise RuntimeError("unexpected DocBook man backend: " + required)
    # OpenSP ships an optional Unicode catalog with its own restrictive
    # SGMLDECL. Debian does not activate that unrelated syntax for DocBook.
    # Select only DocBook DTDs and their ISO character entities.
    catalogs = sorted(path for directory in ("docbook", "entities")
                      for path in (root / "usr/share/sgml" / directory).rglob("catalog"))
    if not catalogs or not any("4.2" in str(path) for path in catalogs):
        raise RuntimeError("missing DocBook 4.2 SGML catalog")
    # Debian catalogs can contain absolute references to their own DTD/entity
    # files. Relocate those references; preserve all catalog declarations.
    for directory in (root / "usr/share/sgml", root / "usr/share/xml"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if b"/usr/share/sgml/" not in data and b"/usr/share/xml/" not in data:
                continue
            for prefix in ("/usr/share/sgml/", "/usr/share/xml/"):
                relative = os.path.relpath(root / prefix.lstrip("/"), path.parent) + "/"
                data = data.replace(prefix.encode(), relative.encode())
            path.write_bytes(data)
    catalog_path = ":".join('$root/' + str(path.relative_to(root)) for path in catalogs)
    script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
PATH="$root/bin"
SGML_CATALOG_FILES="''' + catalog_path + '''"
SGML_SEARCH_PATH="$root/usr/share/sgml"
LC_ALL=C
export PATH SGML_CATALOG_FILES SGML_SEARCH_PATH LC_ALL
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
if [ "$#" = 1 ] && [ "$1" = --version ]; then
    printf '%s\\n' 'DocBook-utils man backend (OpenSP and SGMLS)'
    exit 0
fi
if [ "$#" != 1 ] || [ ! -f "$1" ]; then
    printf '%s\\n' 'Usage: docbook2man input.sgml' >&2
    exit 2
fi
: "${SOURCE_DATE_EPOCH:?set SOURCE_DATE_EPOCH for reproducible manuals}"
export SOURCE_DATE_EPOCH
temporary=$(mktemp -d "${TMPDIR:-/tmp}/sonic-docbook.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
onsgmls "$1" > "$temporary/document.esis"
if ! "$root/bin/sgmlspl" "$root/usr/share/perl5/sgmlspl-specs/docbook2man-spec.pl" < "$temporary/document.esis" 2> "$temporary/errors"; then
    cat "$temporary/errors" >&2
    exit 1
fi
if grep unresolved "$temporary/errors" >/dev/null 2>&1; then
    "$root/bin/sgmlspl" "$root/usr/share/perl5/sgmlspl-specs/docbook2man-spec.pl" < "$temporary/document.esis"
fi
'''
    write_launcher(root, "docbook2man", script, executable)
    manifest = json.loads((root / "toolchain.json").read_text())
    manifest["tools"]["docbook2man"] = str(specification.relative_to(root))
    manifest["sgml_catalogs"] = [str(path.relative_to(root)) for path in catalogs]
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


def prepare_kerberos(root: Path, architecture: str, loader: Path, libraries: list[Path], executable: Path) -> None:
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
        write_launcher(root, name, launcher, executable)
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


def prepare_image(root, architecture, loader, libraries, executable):
    """Relocate the image tools and their fixed package data into one runtime."""
    paths = {
        "mksquashfs": "usr/bin/mksquashfs", "unsquashfs": "usr/bin/unsquashfs",
        "qemu-img": "usr/bin/qemu-img", "sgdisk": "usr/sbin/sgdisk",
        "mke2fs": "usr/sbin/mke2fs", "debugfs": "usr/sbin/debugfs",
        "mkfs.fat": "usr/sbin/mkfs.fat", "mcopy": "usr/bin/mcopy", "mmd": "usr/bin/mmd",
        "tar-split": "usr/bin/tar-split", "grub-mkimage": "usr/bin/grub-mkimage",
        "grub-editenv": "usr/bin/grub-editenv", "openssl": "usr/bin/openssl",
        "bwrap": "usr/bin/bwrap", "zstd": "usr/bin/zstd",
        "depmod": "usr/sbin/depmod", "modprobe": "usr/sbin/modprobe",
        "busybox": "usr/bin/busybox",
    }
    if architecture == "amd64":
        paths["grub-bios-setup"] = "usr/lib/grub/i386-pc/grub-bios-setup"
        paths["qemu-system-x86_64"] = "usr/bin/qemu-system-x86_64"
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    # Debian's /bin -> usr/bin is an install alias. Wrappers need their own
    # directory so they cannot overwrite the original ELF in usr/bin, and their
    # adjacent scripts resolve the runtime root at bin/.. on every invocation.
    wrappers = root / "bin"
    if wrappers.is_symlink():
        wrappers.unlink()
    wrappers.mkdir(exist_ok=True)
    for name, path in paths.items():
        target = root / path
        if not target.is_file():
            raise ValueError("image tool missing from locked packages: " + path)
        data = target.read_bytes()
        if len(data) < 64 or data[:4] != b"\x7fELF":
            raise ValueError("image executable is not ELF: " + path)
        if struct.unpack_from("<H", data, 18)[0] != {"amd64": 62, "arm64": 183}[architecture]:
            raise ValueError("image tool architecture mismatch: " + path)
        offset = struct.unpack_from("<Q", data, 32)[0]
        size, count = struct.unpack_from("<HH", data, 54)
        dynamic = any(struct.unpack_from("<I", data, offset + index * size)[0] == 3 for index in range(count))
        script = """#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
MKE2FS_CONFIG="$root/etc/mke2fs.conf"
OPENSSL_CONF="$root/etc/ssl/openssl.cnf"
export MKE2FS_CONFIG OPENSSL_CONF
"""
        suffix = ""
        if name == "qemu-system-x86_64":
            # QEMU's -L adds a firmware directory. Debian splits firmware
            # between qemu-system-data, SeaBIOS and iPXE packages.
            for directory in ("usr/share/qemu", "usr/share/seabios", "usr/lib/ipxe/qemu"):
                if (root / directory).is_dir():
                    suffix += ' -L "$root/' + directory + '"'
            if not (root / "usr/share/seabios/bios-256k.bin").is_file():
                raise ValueError("declared QEMU runtime lacks its PC BIOS")
        if name == "qemu-system-x86_64":
            module_directories = sorted((root / "usr/lib").glob("*/qemu"))
            if len(module_directories) != 1:
                raise ValueError("expected one declared native QEMU module directory")
            script += 'QEMU_MODULE_DIR="$root/' + str(module_directories[0].relative_to(root)) + '"\nexport QEMU_MODULE_DIR\n'
        if dynamic:
            script += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" "$root/' + path + '"' + suffix + ' "$@"\n'
        else:
            script += 'exec "$root/' + path + '"' + suffix + ' "$@"\n'
        write_launcher(root, name, script, executable)
    manifest = {"format_version": 1, "profile": "image", "architecture": architecture,
                "loader": str(loader.relative_to(root)),
                "library_directories": [str(path.relative_to(root)) for path in libraries], "tools": paths}
    # Optional certificate stores and alternatives are generated by package
    # installation, while this profile consumes package data directly. Retain
    # an explicit audit of absent optional links instead of emitting invalid
    # Bazel tree artifacts. Tool executables above are mandatory and validated.
    dangling = [path for path in root.rglob("*") if path.is_symlink() and not path.exists()]
    manifest["omitted_unresolved_symlinks"] = sorted(str(path.relative_to(root)) for path in dangling)
    for path in dangling:
        path.unlink()
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


def prepare_manuals(root, architecture, loader, libraries, executable):
    """Keep Sphinx and Texinfo on their declared Python/Perl/data closure."""
    wrappers = root / "bin"
    if wrappers.is_symlink():
        wrappers.unlink()
    wrappers.mkdir(exist_ok=True)
    prefix = [str(loader), "--library-path", os.pathsep.join(map(str, libraries))]
    python = root / "usr/bin/python3"
    perl = root / "usr/bin/perl"
    environment = {"LANG": "C", "LC_ALL": "C", "PATH": ""}
    builtins = run_tool(prefix + [str(perl), "-e", 'print join("\\0", @INC)'], environment=environment).decode().split("\0")
    perl_directories = [root / path.lstrip("/") for path in builtins
                        if path.startswith("/") and (root / path.lstrip("/")).is_dir()]
    # Remove the interpreter's ambient host search paths, retaining explicit
    # caller inputs and the declared core/XS modules in their original order.
    bootstrap = root / "share/sonic-build-tools/Sonic/Manuals.pm"
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    quoted = ", ".join("'" + path.replace("'", "\\'") + "'" for path in builtins)
    bootstrap.write_text("package Sonic::Manuals;\nBEGIN { my %host = map { $_ => 1 } (" + quoted + "); @INC = grep { !$host{$_} } @INC; }\n1;\n")
    texinfo_dirs = [root / "usr/share/texinfo"] + sorted((root / "usr/lib").glob("*/texinfo"))
    perl_directories = [bootstrap.parent.parent] + texinfo_dirs + perl_directories
    perl_path = ":".join('$root/' + str(path.relative_to(root)) for path in perl_directories)
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    # Debian's configured module and data roots take precedence over its
    # relocation fallback when a host Texinfo happens to be installed.
    # Bind those configuration values to this declared installation instead.
    for relative, original, relocated in [
        ("docutils/__init__.py", "os.path.join('/usr/share/docutils/', suffix)",
         'os.path.join(os.environ["SONIC_MANUALS_ROOT"], "usr/share/docutils", suffix)'),
        ("sphinx/__init__.py", "package_dir = '/usr/share/sphinx'",
         'package_dir = os.path.join(os.environ["SONIC_MANUALS_ROOT"], "usr/share/sphinx")'),
    ]:
        configuration = root / "usr/lib/python3/dist-packages" / relative
        content = configuration.read_text()
        if original not in content:
            raise ValueError("unsupported documentation data setting: " + relative)
        configuration.write_text(content.replace(original, relocated))
    texi2any = root / "usr/bin/texi2any"
    text = texi2any.read_text()
    for original, relocated in {
        "my $datadir = '/usr/share';": 'my $datadir = "$ENV{SONIC_MANUALS_ROOT}/usr/share";',
        "my $xsdir = '/usr/lib/texinfo';": 'my $xsdir = "$ENV{SONIC_MANUALS_ROOT}/usr/lib/texinfo";',
        "my $prefix = '/usr';": 'my $prefix = "$ENV{SONIC_MANUALS_ROOT}/usr";',
        "eval '\"/usr/etc\"'": '"${prefix}/etc"',
        "eval '\"/usr/share\"'": '"${prefix}/share"',
    }.items():
        if original not in text:
            raise ValueError("unsupported Texinfo installation setting: " + original)
        text = text.replace(original, relocated)
    texi2any.write_text(text)
    tools = {"sphinx-build": "usr/bin/sphinx-build", "makeinfo": "usr/bin/texi2any"}
    helpers = ["perl", "python3", "bash", "gzip", "sed", "grep", "cat", "dirname", "basename", "sort", "cut", "head", "tr", "rm", "mkdir", "mv", "cp", "locale"]
    for name in helpers:
        path = root / "usr/bin" / name
        if not path.is_file():
            raise ValueError("manual tools package closure lacks " + name)
        tools[name] = "usr/bin/" + name
    for name, path in tools.items():
        target = root / path
        if not target.is_file():
            raise ValueError("manual tool missing from locked packages: " + path)
        script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
PATH="$root/bin"
SONIC_MANUALS_ROOT="$root"
LOCPATH="$root/usr/lib/locale"
export PATH SONIC_MANUALS_ROOT LOCPATH
'''
        if name in ("sphinx-build", "python3"):
            script += 'PYTHONHOME="$root/usr"\nPYTHONPATH="$root/usr/lib/python3/dist-packages"\nPYTHONNOUSERSITE=1\nexport PYTHONHOME PYTHONPATH PYTHONNOUSERSITE\n'
            command = '"$root/' + str(python.relative_to(root)) + '" -I'
            if name == "sphinx-build":
                command += ' "$root/' + path + '"'
        elif name in ("makeinfo", "perl"):
            script += 'PERL5LIB="' + perl_path + '"\nPERL5OPT="-MSonic::Manuals"\nexport PERL5LIB PERL5OPT\n'
            command = '"$root/' + str(perl.relative_to(root)) + '"'
            if name == "makeinfo":
                command += ' "$root/' + path + '"'
        else:
            command = '"$root/' + path + '"'
        script += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" ' + command + ' "$@"\n'
        write_launcher(root, name, script, executable)
    versions = {name: run_tool([str(root / "bin" / name), "--version"], environment=environment).decode().splitlines()[0]
                for name in ("sphinx-build", "makeinfo")}
    # Installation-created alternatives/certificate links are not inputs to
    # these frontends. Do not emit dangling tree members into Bazel's cache.
    dangling = [path for path in root.rglob("*") if path.is_symlink() and not path.exists()]
    for path in dangling:
        path.unlink()
    (root / "toolchain.json").write_text(json.dumps({"format_version": 1, "profile": "manuals",
        "architecture": architecture, "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": tools, "versions": versions,
        "omitted_unresolved_symlinks": sorted(str(path.relative_to(root)) for path in dangling)}, indent=2) + "\n")



def prepare_tcl(root: Path, architecture: str, loader: Path, libraries: list[Path], executable: Path) -> None:
    """Expose Tcl and Expect with their declared standard library and packages."""
    standard_library = root / "usr/share/tcltk/tcl8.6"
    if not (standard_library / "init.tcl").is_file():
        raise RuntimeError("Tcl packages contain no Tcl 8.6 standard library")
    extension_indexes = sorted(root.glob("usr/lib/tcltk/*/expect*/pkgIndex.tcl"))
    if len(extension_indexes) != 1:
        raise RuntimeError("Tcl packages must contain one Expect package index")
    index = extension_indexes[0]
    original = index.read_text()
    pattern = r"\[file join /usr lib ([a-zA-Z0-9_-]+) (libexpect\.so\.[0-9.]+)\]"
    match = re.search(pattern, original)
    if match is None or not (root / "usr/lib" / match[1] / match[2]).is_file():
        raise RuntimeError("Unexpected Expect package library reference")
    # The Debian package index uses an installation-absolute path. Keep its
    # package/version declaration and load the same library relative to $dir.
    relative = os.path.relpath(root / "usr/lib" / match[1] / match[2], index.parent)
    replacement = "[file join $dir " + " ".join(Path(relative).parts) + "]"
    index.write_text(original[:match.start()] + replacement + original[match.end():])
    tools = {"tclsh": "usr/bin/tclsh8.6"}
    (root / "bin").mkdir(exist_ok=True)
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    package_directory = str(extension_indexes[0].parent.parent.relative_to(root))
    for name, path in tools.items():
        if not (root / path).is_file():
            raise RuntimeError(f"Tcl packages contain no {path}")
        script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH TCL_LIBRARY TCLLIBPATH
TCL_LIBRARY="$root/usr/share/tcltk/tcl8.6"
TCL8_6_TM_PATH="$TCL_LIBRARY/tcl8"
'''
        # Tcl list quoting keeps relocated runtime paths with spaces intact.
        script += 'TCLLIBPATH="{'+ '$root/' + package_directory + '}"\n'
        script += 'PATH="$root/bin"\nexport TCL_LIBRARY TCLLIBPATH TCL8_6_TM_PATH PATH\n'
        script += 'exec "$root/' + str(loader.relative_to(root)) + '" --inhibit-cache --library-path "' + library_path + '" "$root/' + path + '" "$@"\n'
        write_launcher(root, name, script, executable)
    # tzdata's optional localtime alias targets an installation-owned /etc
    # file, outside the interpreter's declared standard library and packages.
    dangling = [path for path in root.rglob("*") if path.is_symlink() and not path.exists()]
    omitted = sorted(str(path.relative_to(root)) for path in dangling)
    for path in dangling:
        path.unlink()
    (root / "toolchain.json").write_text(json.dumps({
        "omitted_unresolved_symlinks": omitted,
        "format_version": 1, "profile": "tcl", "architecture": architecture,
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": tools,
    }, indent=2) + "\n")


def prepare_gdb(root, architecture, loader, libraries, executable):
    """Use the locked GDB, embedded Python and data without host runtime paths."""
    target = root / "usr/bin/gdb"
    require_files = [target, root / "usr/share/gdb/python/gdb/__init__.py", root / "usr/lib/python3.13/os.py"]
    if not all(path.is_file() for path in require_files):
        raise ValueError("declared GDB runtime lacks its executable, data or Python standard library")
    if struct.unpack_from("<H", target.read_bytes(), 18)[0] != {"amd64": 62, "arm64": 183}[architecture]:
        raise ValueError("GDB runtime architecture mismatch")
    wrappers = root / "bin"
    if wrappers.is_symlink():
        wrappers.unlink()
    wrappers.mkdir(exist_ok=True)
    library_path = ":".join('$root/' + str(path.relative_to(root)) for path in libraries)
    script = """#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_PROFILE LD_ORIGIN_PATH
PYTHONHOME="$root/usr"
PYTHONPATH="$root/usr/share/gdb/python"
PYTHONNOUSERSITE=1
TZ=UTC
export PYTHONHOME PYTHONPATH PYTHONNOUSERSITE TZ
"""
    gconv = [path for path in libraries if path.name == "gconv"]
    if not gconv or not any((path / "UTF-32.so").is_file() for path in gconv):
        raise ValueError("declared GDB runtime lacks its glibc character-conversion modules")
    script += 'GCONV_PATH="' + ":".join('$root/' + str(path.relative_to(root)) for path in gconv) + '"\nexport GCONV_PATH\n'
    script += 'exec "$root/' + str(loader.relative_to(root)) + '" --library-path "' + library_path + '" "$root/usr/bin/gdb" --data-directory="$root/usr/share/gdb" "$@"\n'
    write_launcher(root, "gdb", script, executable)
    # tzdata's compatibility link is normally created by package configuration.
    # GDB runs with the explicit UTC environment above and does not consume it.
    optional = root / "usr/share/zoneinfo/localtime"
    omitted = []
    if optional.is_symlink() and not optional.exists():
        omitted.append({"path": str(optional.relative_to(root)), "target": os.readlink(optional)})
        optional.unlink()
    (root / "toolchain.json").write_text(json.dumps({
        "format_version": 1, "profile": "gdb", "architecture": architecture,
        "loader": str(loader.relative_to(root)),
        "library_directories": [str(path.relative_to(root)) for path in libraries],
        "tools": {"gdb": "usr/bin/gdb"}, "omitted_optional_links": omitted,
    }, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--launcher", required=True, type=Path)
    parser.add_argument("--tar", action="append", default=[])
    parser.add_argument("--profile", choices=("documentation", "configure", "kerberos", "foreign", "image", "manuals", "tcl", "gdb", "tests", "perl", "docbook"), default="documentation")
    args = parser.parse_args()
    validate_launcher(args.launcher, args.architecture)
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for payload in sorted(map(Path, args.tar)):
        extract_payload(root, payload)

    if args.profile == "image":
        # Keep wrappers separate from the package's /bin -> usr/bin alias.
        wrappers = root / "bin"
        if wrappers.is_symlink():
            wrappers.unlink()
        wrappers.mkdir(exist_ok=True)

    if args.profile in ("configure", "foreign", "manuals", "image", "tests", "perl", "docbook"):
        # Bazel caches files inside tree outputs. Materialize directory aliases
        # (notably Perl's 5.40 -> 5.40.1) so cache restoration never has to create
        # a child through a directory symlink before its target is populated.
        while True:
            links = [path for path in root.rglob("*") if path.is_symlink() and path.is_dir()]
            if not links:
                break
            for path in links:
                target = path.resolve(strict=True)
                # X11 packages keep a compatibility alias /usr/bin/X11 -> .;
                # none of the declared tools uses this recursive directory.
                if args.profile in ("foreign", "image") and path in (root / "usr/bin/X11", root / "bin/X11") and target == path.parent:
                    path.unlink()
                    continue
                if not target.is_relative_to(root) or path.is_relative_to(target):
                    raise ValueError(f"invalid runtime directory alias: {path}")
                path.unlink()
                shutil.copytree(target, path, symlinks=True)
    loader = loader_path(root, args.architecture)
    libraries = library_directories(root)
    if args.profile == "gdb":
        prepare_gdb(root, args.architecture, loader, libraries, args.launcher)
        return
    if args.profile == "image":
        prepare_image(root, args.architecture, loader, libraries, args.launcher)
        return
    if args.profile == "manuals":
        prepare_manuals(root, args.architecture, loader, libraries, args.launcher)
        return
    if args.profile == "tcl":
        prepare_tcl(root, args.architecture, loader, libraries, args.launcher)
        return
    prefix = [str(loader), "--library-path", os.pathsep.join(map(str, libraries))]
    if args.profile in ("configure", "foreign", "perl", "docbook"):
        prepare_configure(root, args.architecture, loader, libraries, args.launcher, foreign=args.profile == "foreign", perl_only=args.profile == "perl", docbook=args.profile == "docbook")
        return
    if args.profile == "tests":
        from test_runtime import prepare_tests
        prepare_tests(root, args.architecture, loader, libraries)
        return
    if args.profile == "kerberos":
        prepare_kerberos(root, args.architecture, loader, libraries, args.launcher)
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
        write_launcher(root, name, script, args.launcher)
    (root / "toolchain.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
