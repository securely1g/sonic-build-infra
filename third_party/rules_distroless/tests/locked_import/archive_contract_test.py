"""Inspect existing Debian archives imported through the public module extension."""
import argparse
import hashlib
import json
import struct
import tarfile


def inventory(path):
    with tarfile.open(path) as archive:
        return {m.name: (m.type.hex(), m.mode, m.uid, m.gid, m.linkname,
                         hashlib.sha256(archive.extractfile(m).read()).hexdigest() if m.isfile() else None)
                for m in archive}


def control(path):
    with tarfile.open(path) as archive:
        member = next(m for m in archive if m.name.lstrip('./') == 'control')
        return dict(line.split(': ', 1) for line in archive.extractfile(member).read().decode().splitlines()
                    if ': ' in line and not line.startswith(' '))


parser = argparse.ArgumentParser()
for field in ['lock', 'arch', 'data', 'copy', 'libc-data', 'libc-control', 'lua-control', 'all-control', 'lua-closure']:
    parser.add_argument('--' + field, required=True, **({} if field in ['lock', 'arch'] else {'nargs': '+'}))
args = parser.parse_args()
lock = json.load(open(args.lock))
expected = {p['name']: p for key, p in lock['packages'].items() if ':' + args.arch + '=' in key}
data = args.data
copy = args.copy
assert len(data) == len(copy) == len(expected), (len(data), len(expected))
# Independent namespaces can import the same archive graph without repository
# collisions or changes to file bytes, ownership, modes and links.
assert sorted(json.dumps(inventory(p), sort_keys=True) for p in data) == sorted(json.dumps(inventory(p), sort_keys=True) for p in copy)
assert len(args.libc_data) == len(args.libc_control) == 1
for package, paths in [('libc6', args.libc_control), ('liblua5.1-0', args.lua_control), ('netbase', args.all_control)]:
    assert len(paths) == 1
    fields = control(paths[0])
    assert fields['Package'] == package
    assert fields['Version'] == expected[package]['version']
    assert fields['Architecture'] == expected[package]['architecture']
assert len(args.lua_closure) == len(expected) - 1  # netbase is an independent root
found_lua = found_services = False
for path in data:
    with tarfile.open(path) as archive:
        for member in archive:
            name = member.name.lstrip('./')
            if name == 'etc/services':
                found_services = True
            if member.isfile() and name.endswith('/liblua5.1.so.0.0.0'):
                header = archive.extractfile(member).read(20)
                assert header[:4] == b'\x7fELF'
                assert struct.unpack('<H', header[18:20])[0] == {'amd64': 62, 'arm64': 183}[args.arch]
                found_lua = True
assert found_lua and found_services
print('PASS: independent data/control targets, complete closures, locked versions, Architecture: all, and native ELF architecture')
