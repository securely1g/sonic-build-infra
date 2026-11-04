"""Relocatable native Valgrind and Tcl/Expect execution environment."""
import json
import os
from elf_metadata import Elf
from pathlib import Path


def prepare_tests(root, architecture, loader, libraries):
    workers = [path for path in [root / 'usr/libexec/valgrind', root / 'usr/lib/valgrind'] if list(path.glob('memcheck-*-linux'))]
    if len(workers) != 1:
        raise RuntimeError('expected one declared Valgrind worker directory')
    valgrind = root / 'usr/bin/valgrind.bin'
    if not valgrind.is_file():
        valgrind = root / 'usr/bin/valgrind'
    if valgrind.read_bytes()[:4] != b'\x7fELF':
        raise RuntimeError('expected declared Valgrind ELF launcher')
    tcl = sorted(root.glob('usr/share/tcltk/tcl*/init.tcl'))
    if len(tcl) != 1:
        raise RuntimeError('expected one declared Tcl script library')
    packages = sorted({path.parent for path in root.rglob('pkgIndex.tcl')})
    # Debian Expect's pkgIndex embeds /usr/lib. Resolve that declared plugin
    # relative to the index so every Tcl child interpreter remains relocatable.
    for index in root.rglob('pkgIndex.tcl'):
        content = index.read_text()
        if 'package ifneeded Expect ' not in content:
            continue
        import re
        def relocate(match):
            target = root/'usr'/Path(*match.group(1).split())
            if not target.is_file():
                raise RuntimeError('missing declared Tcl plugin: ' + str(target))
            return '[file join $dir ' + os.path.relpath(target, index.parent) + ']'
        content, count = re.subn(r'\[file join /usr ([^\]]+)\]', relocate, content)
        if not count:
            raise RuntimeError('unsupported declared Expect package index')
        index.write_text(content)
    tools = {'valgrind': valgrind, 'tclsh': root / 'usr/bin/tclsh8.6'}
    library_path = ':'.join('$root/' + str(path.relative_to(root)) for path in libraries)
    (root / 'bin').mkdir(exist_ok=True)
    for name, binary in tools.items():
        if not binary.is_file():
            raise RuntimeError('declared test packages contain no ' + name)
        script = '''#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
root=$(CDPATH= cd -- "${self%/*}/.." && pwd -P)
unset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT
'''
        if name == 'valgrind':
            script += 'VALGRIND_LIB="$root/' + str(workers[0].relative_to(root)) + '"\nVALGRIND_LAUNCHER="$root/bin/valgrind"\nexport VALGRIND_LIB VALGRIND_LAUNCHER\n'
        else:
            script += 'TCL_LIBRARY="$root/' + str(tcl[0].parent.relative_to(root)) + '"\n'
            script += 'TCLLIBPATH="' + ' '.join('{$root/' + str(path.relative_to(root)) + '}' for path in packages) + '"\nexport TCL_LIBRARY TCLLIBPATH\n'
            script += 'TCL8_6_TM_PATH="$root/' + str((tcl[0].parent / 'tcl8').relative_to(root)) + '"\nexport TCL8_6_TM_PATH\n'
        script += 'exec "$root/' + str(loader.relative_to(root)) + '"' + (' --argv0 "$root/bin/tclsh"' if name == 'tclsh' else '') + ' --library-path "' + library_path + '" "$root/' + str(binary.relative_to(root)) + '" "$@"\n'
        (root / 'bin' / name).write_text(script)
        (root / 'bin' / name).chmod(0o755)
    # GNU debuglink lookup is object-relative. Valgrind's extra-debuginfo-path
    # prepends the absolute object path, so a relocated Debian build-id tree
    # alone is insufficient. Add standard adjacent .debug links by identity.
    for binary in list(root.rglob('*.so*')):
        if not binary.is_file() or binary.read_bytes()[:4] != b'\x7fELF':
            continue
        elf = Elf(binary.read_bytes())
        if not elf.build_id or not elf.debuglink:
            continue
        debug = root/'usr/lib/debug/.build-id'/elf.build_id[:2]/(elf.build_id[2:]+'.debug')
        if debug.is_file():
            link = binary.parent/'.debug'/elf.debuglink
            link.parent.mkdir(exist_ok=True)
            if not link.exists():
                link.symlink_to(os.path.relpath(debug, link.parent))
    dangling = [path for path in root.rglob('*') if path.is_symlink() and not path.exists()]
    for path in dangling:
        path.unlink()
    (root / 'toolchain.json').write_text(json.dumps({'format_version': 1, 'profile': 'tests', 'architecture': architecture, 'omitted_unresolved_symlinks': [str(path.relative_to(root)) for path in dangling], 'tools': {name: str(binary.relative_to(root)) for name, binary in tools.items()}, 'loader': str(loader.relative_to(root)), 'library_directories': [str(path.relative_to(root)) for path in libraries]}, indent=2) + '\n')
