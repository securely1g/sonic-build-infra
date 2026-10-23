"""Relocate the declared Debian packaging tools without changing package paths."""

from pathlib import Path
import shlex


def _replace(path: Path, before: str, after: str, count: int = 1) -> None:
    text = path.read_text()
    if text.count(before) != count:
        raise ValueError(f"unexpected declared Debian tool: {path.name}")
    path.write_text(text.replace(before, after))


def _python_tools(root: Path) -> None:
    """Relocate build-time lookups, retaining installed Python package paths."""
    dhpython = root / "usr/share/dh-python/dhpython"
    debpython = root / "usr/share/python3/debpython"
    _replace(dhpython / "__init__.py", "import re\n", "import re\nfrom os import environ\n")
    _replace(dhpython / "__init__.py", "'cpython3': '/usr/share/python3/dist/',",
             "'cpython3': environ['SONIC_BUILD_TOOLS_ROOT'] + '/usr/share/python3/dist/',")
    _replace(dhpython / "_defaults.py",
             'config.read("/usr/share/python{}/debian_defaults".format(ver))',
             'config.read(environ["SONIC_BUILD_TOOLS_ROOT"] + "/usr/share/python{}/debian_defaults".format(ver))')
    _replace(dhpython / "debhelper.py",
             'fpath = "/usr/share/debhelper/autoscripts/%s" % tpl_name',
             'fpath = environ["SONIC_BUILD_TOOLS_ROOT"] + "/usr/share/debhelper/autoscripts/%s" % tpl_name')
    _replace(dhpython / "pydist.py",
             "os.environ.get('DH_PYTHON_DIST', '/usr/share/dh-python/dist/')",
             "os.environ.get('DH_PYTHON_DIST', os.environ['SONIC_BUILD_TOOLS_ROOT'] + '/usr/share/dh-python/dist/')")
    _replace(dhpython / "pydist.py", "('/usr/bin/dpkg', '-S', dpkg_query)",
             "(os.environ['SONIC_BUILD_TOOLS_ROOT'] + '/bin/dpkg', '-S', dpkg_query)")
    _replace(dhpython / "tools.py", "if exists('/usr/bin/dpkg-architecture'):",
             "if exists(os.environ['SONIC_BUILD_TOOLS_ROOT'] + '/bin/dpkg-architecture'):")
    _replace(dhpython / "tools.py", "res = execute('/usr/bin/dpkg-architecture')",
             "res = execute([os.environ['SONIC_BUILD_TOOLS_ROOT'] + '/bin/dpkg-architecture'], shell=False)")
    # Interpreter.path/binary() describe installed shebangs. Change only the
    # subprocess used to inspect the build interpreter, never those values.
    _replace(dhpython / "interpreter.py",
             'exe = "{}{}".format(self.path, self._vstr(version))',
             'exe = os.path.join(os.environ["SONIC_BUILD_TOOLS_ROOT"], "bin", self._vstr(version))')
    _replace(dhpython / "interpreter.py",
             'command = "{} -c \'{}\'".format(exe, command.replace("\'", "\\\'"))',
             'command = (exe, "-c", command)')
    _replace(dhpython / "interpreter.py", "output = execute(command)",
             "output = execute(command, shell=False)")
    _replace(debpython / "version.py", "_config.read('/usr/share/python3/debian_defaults')",
             "_config.read(environ['SONIC_BUILD_TOOLS_ROOT'] + '/usr/share/python3/debian_defaults')")
    _replace(debpython / "version.py", 'exists("/usr/bin/python%d.%d" % v)',
             'exists(environ["SONIC_BUILD_TOOLS_ROOT"] + "/bin/python%d.%d" % v)', count=2)
    _replace(debpython / "files.py", "('/usr/bin/dpkg', '-L', package_name)",
             "(environ['SONIC_BUILD_TOOLS_ROOT'] + '/bin/dpkg', '-L', package_name)")
    for name in ("py3clean", "py3compile"):
        _replace(root / "usr/bin" / name, "sys.path.insert(1, '/usr/share/python3/')",
                 "sys.path.insert(1, environ['SONIC_BUILD_TOOLS_ROOT'] + '/usr/share/python3/')")
    _replace(root / "usr/bin/py3compile", "name='/usr/share/python3/bcep/'",
             "name=os.environ['SONIC_BUILD_TOOLS_ROOT'] + '/usr/share/python3/bcep/'")
    _replace(root / "usr/bin/py3compile", 'cmd = ["/usr/bin/python" + version]',
             'cmd = [os.environ["SONIC_BUILD_TOOLS_ROOT"] + "/bin/python" + version]')
    _replace(root / "usr/bin/py3compile", 'exists("/usr/bin/python%d.%d" % options.vrange[0])',
             'exists(os.environ["SONIC_BUILD_TOOLS_ROOT"] + "/bin/python%d.%d" % options.vrange[0])')


def _private_scripts(root: Path, directory: str) -> None:
    """Route executable package helpers through the declared interpreters.

    Data files and maintainer-script templates are deliberately left intact.
    The wrapper contains no build-directory path, so a cached tree can move.
    """
    for path in sorted((root / directory).rglob("*")):
        if path.is_symlink() or not path.is_file() or not path.stat().st_mode & 0o111:
            continue
        with path.open("rb") as stream:
            first = stream.readline()
        if not first.startswith(b"#!"):
            continue
        command = shlex.split(first[2:].decode().strip())
        if not command:
            raise ValueError(f"empty declared helper interpreter: {path}")
        if Path(command[0]).name == "env":
            command = command[1:]
            if command and command[0] == "-S":
                command = command[1:]
        interpreter = Path(command[0]).name if command else ""
        if interpreter not in {"sh", "bash", "dash", "perl", "python3"}:
            raise ValueError(f"unsupported declared helper interpreter: {path}")
        relative = path.relative_to(root)
        saved = Path("share/sonic-build-tools/debian-scripts") / relative
        destination = root / saved
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError(f"declared helper was already relocated: {path}")
        path.rename(destination)
        arguments = " ".join(shlex.quote(value) for value in command[1:])
        path.write_text(
            '#!/bin/sh\nset -eu\n'
            ': "${SONIC_BUILD_TOOLS_ROOT:?missing declared build-tools root}"\n'
            f'exec "$SONIC_BUILD_TOOLS_ROOT/bin/{interpreter}" {arguments} '
            f'"$SONIC_BUILD_TOOLS_ROOT/{saved}" "$@"\n'
        )
        path.chmod(0o755)


def _quilt_compat(root: Path) -> None:
    # Quilt prepends these aliases to PATH. Its awk symlink otherwise reaches
    # the raw Debian ELF; symlinking to bin/awk instead also gives the launcher
    # the wrong $0. Dispatch through the original declared entrypoint.
    for name in ("awk", "bash"):
        path = root / "usr/share/quilt/compat" / name
        if not path.is_symlink():
            raise ValueError("unexpected declared Quilt compatibility alias: " + name)
        path.unlink()
        path.write_text(
            '#!/bin/sh\nset -eu\n'
            ': "${SONIC_BUILD_TOOLS_ROOT:?missing declared build-tools root}"\n'
            f'exec "$SONIC_BUILD_TOOLS_ROOT/bin/{name}" "$@"\n'
        )
        path.chmod(0o755)


def prepare_debian(root: Path) -> None:
    """Adapt only a newly assembled runtime; never mutate an input runtime.

    Public tool launchers supply SONIC_BUILD_TOOLS_ROOT. The kernel action may
    select DPKG_ADMINDIR containing a private copy of the library file lists
    with their relocated paths; the remaining package metadata stays unchanged.
    """
    root = Path(root)
    _python_tools(root)
    dpkg = root / "usr/share/perl5/Dpkg.pm"
    _replace(
        dpkg,
        "our $CONFDIR = '/etc/dpkg';\n"
        "our $ADMINDIR = '/var/lib/dpkg';\n"
        "our $LIBDIR = '/usr/lib/dpkg';\n"
        "our $DATADIR = '/usr/share/dpkg';",
        "my $sonic_root = $ENV{SONIC_BUILD_TOOLS_ROOT};\n"
        'die "missing declared build-tools root\\n" unless defined($sonic_root) && length($sonic_root);\n'
        'our $CONFDIR = "$sonic_root/etc/dpkg";\n'
        'our $ADMINDIR = $ENV{DPKG_ADMINDIR} // "$sonic_root/var/lib/dpkg";\n'
        'our $LIBDIR = "$sonic_root/usr/lib/dpkg";\n'
        'our $DATADIR = "$sonic_root/usr/share/dpkg";',
    )
    # Never search the build host's libraries or ld.so configuration. Explicit
    # package build directories and -l options retain their upstream semantics.
    # Prefer Debian's canonical merged-/usr package paths: cached tree outputs
    # can materialize /lib as a directory rather than preserving its symlink.
    _replace(
        root / "usr/share/perl5/Dpkg/Shlibs.pm",
        "sub setup_library_paths {\n"
        "    @custom_librarypaths = ();\n"
        "    @system_librarypaths = ();",
        "sub setup_library_paths {\n"
        "    @custom_librarypaths = ();\n"
        "    @system_librarypaths = ();\n"
        "    if (my $root = $ENV{SONIC_DPKG_SHLIBS_ROOT} // $ENV{SONIC_BUILD_TOOLS_ROOT}) {\n"
        "        my $multiarch = debarch_to_multiarch(get_host_arch());\n"
        '        @system_librarypaths = map { "$root$_" } (\n'
        '            "/usr/lib/$multiarch", "/lib/$multiarch",\n'
        "            DEFAULT_LIBRARY_PATH, DEFAULT_MULTILIB_PATH);\n"
        "        $librarypaths_init = 1;\n"
        "        return;\n"
        "    }",
    )
    # An ELF's RPATH may be absolute. The OCI metadata action supplies its
    # actual target package trees so that even RPATH lookup cannot select the
    # build host or an execution tool's private library by accident.
    _replace(
        root / "usr/share/perl5/Dpkg/Shlibs.pm",
        '\tmy $checkdir = "$root$dir";\n',
        '\tmy $checkdir = "$root$dir";\n'
        '        if (my $declared = $ENV{SONIC_DPKG_TARGET_ROOTS}) {\n'
        '            require Cwd;\n'
        '            my $candidate = Cwd::abs_path("$checkdir/$lib");\n'
        '            next unless defined $candidate;\n'
        '            next unless grep {\n'
        '                my $allowed = Cwd::abs_path($_);\n'
        '                defined($allowed) && index($candidate, "$allowed/") == 0\n'
        '            } split(/\\n/, $declared);\n'
        '        }\n',
    )
    _replace(
        root / "usr/share/perl5/Debian/Debhelper/Dh_Lib.pm",
        'my $prefix="/usr";',
        'my $prefix = ($ENV{SONIC_BUILD_TOOLS_ROOT} // die "missing declared build-tools root\\n") . "/usr";',
    )
    for relative in ("usr/bin/kernel-wedge", "usr/share/kernel-wedge/commands/help"):
        _replace(
            root / relative,
            "LIBDIR=/usr/share/kernel-wedge/commands",
            'LIBDIR="$SONIC_BUILD_TOOLS_ROOT/usr/share/kernel-wedge/commands"',
        )
    # Quilt already supports QUILT_DIR. Relocate its defaults as well, including
    # secondary scripts that can be invoked independently of the main command.
    for relative in ("usr/bin/quilt", "usr/share/quilt/scripts/backup-files"):
        _replace(
            root / relative,
            ": ${QUILT_DIR=/usr/share/quilt}",
            ': "${QUILT_DIR:=$SONIC_BUILD_TOOLS_ROOT/usr/share/quilt}"',
        )
    _replace(
        root / "usr/bin/quilt",
        "$HOME/.quiltrc /etc/quilt.quiltrc",
        '$HOME/.quiltrc "$SONIC_BUILD_TOOLS_ROOT/etc/quilt.quiltrc"',
    )
    for path in [root / "usr/bin/quilt", *(root / "usr/share/quilt").rglob("*")]:
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        text = data.decode()
        text = text.replace("export TEXTDOMAINDIR=/usr/share/locale",
                            'export TEXTDOMAINDIR="$SONIC_BUILD_TOOLS_ROOT/usr/share/locale"')
        text = text.replace('bindtextdomain("quilt", "/usr/share/locale")',
                            'bindtextdomain("quilt", "$ENV{SONIC_BUILD_TOOLS_ROOT}/usr/share/locale")')
        if text.encode() != data:
            path.write_text(text)
    # generic-rules is consumed as a Makefile and must remain Makefile text.
    for directory in ("usr/lib/dpkg", "usr/share/kernel-wedge/commands", "usr/share/quilt"):
        _private_scripts(root, directory)
    _quilt_compat(root)
