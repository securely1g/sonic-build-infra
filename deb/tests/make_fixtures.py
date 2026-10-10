#!/usr/bin/env python3
"""Regenerate small checked-in input packages outside Bazel (never a build target)."""
import bz2
import gzip
import hashlib
import io
import json
import lzma
from pathlib import Path
import tarfile

ROOT = Path(__file__).parent / 'fixtures'


def tar(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, data, kind, mode in entries:
            member = tarfile.TarInfo(name)
            member.uid, member.gid = 123, 456
            member.uname, member.gname = 'sonic-test', 'sonic-test-group'
            member.mode, member.mtime = mode, 1700000000
            member.type = kind
            member.pax_headers = {'SCHILY.xattr.user.example': 'preserved'}
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = data
            elif kind == tarfile.REGTYPE:
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return output.getvalue()


def ar(members):
    result = bytearray(b'!<arch>\n')
    for name, contents in members:
        result.extend(f'{name:<16}{0:<12}{0:<6}{0:<6}{0o100644:<8o}{len(contents):<10}`\n'.encode())
        result.extend(contents)
        if len(contents) % 2:
            result.extend(b'\n')
    return result


def main():
    ROOT.mkdir(exist_ok=True)
    hashes = {}
    for kind, suffix, compress, architecture in [('gz', '.gz', lambda b: gzip.compress(b, mtime=0), 'all'),
                                                ('xz', '.xz', lzma.compress, 'all'),
                                                ('bz2', '.bz2', bz2.compress, 'all'),
                                                ('raw', '', lambda b: b, 'all'),
                                                ('foreign', '.gz', lambda b: gzip.compress(b, mtime=0), 'arm64')]:
        package = 'example-' + kind
        control = tar([
            ('./control', (f'Package: {package}\nVersion: 1.0-1\nArchitecture: {architecture}\n'
                          'Depends: libc6 (>= 2.38), alternate-a | alternate-b\n'
                          'Pre-Depends: base-files\nMaintainer: Test <test@example.invalid>\n'
                          'Description: example import package\n preserves multiline fields\n').encode(), tarfile.REGTYPE, 0o644),
            ('./postinst', b'#!/bin/sh\nexit 99\n', tarfile.REGTYPE, 0o755),
        ])
        data = tar([
            ('./usr/share/example/', b'', tarfile.DIRTYPE, 0o750),
            ('./usr/share/example/value', kind.encode(), tarfile.REGTYPE, 0o640),
            ('./usr/share/example/symlink', 'value', tarfile.SYMTYPE, 0o777),
            ('./usr/share/example/hardlink', './usr/share/example/value', tarfile.LNKTYPE, 0o640),
        ])
        contents = ar([('debian-binary', b'2.0\n'), ('control.tar.gz', gzip.compress(control, mtime=0)),
                       ('data.tar' + suffix, compress(data))])
        name = package + '_1.0-1_' + architecture + '.deb'
        (ROOT / name).write_bytes(contents)
        hashes[name] = hashlib.sha256(contents).hexdigest()
    (ROOT / 'sha256.json').write_text(json.dumps(hashes, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
