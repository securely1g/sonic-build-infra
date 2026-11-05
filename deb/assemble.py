"""Assemble one Debian binary package from declared content and control inputs."""
import argparse
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import tarfile
import tempfile

from debian.deb822 import Deb822
from debian.debian_support import Version

ARCHITECTURES = {'amd64': 62, 'arm64': 183, 'armhf': 40}
SCRIPTS = {'preinst', 'postinst', 'prerm', 'postrm', 'config'}
RESERVED = {'control', 'md5sums', 'debian-binary'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def relative(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts, 'unsafe data path: ' + name)
    return str(path)


def fields_from(path):
    records = list(Deb822.iter_paragraphs(path.read_text().splitlines()))
    require(len(records) == 1, 'finalized control must contain exactly one binary package')
    return Deb822(records[0])


def order_payload(source_path, destination):
    """Put declared directory metadata before children for dpkg extraction."""
    with tarfile.open(source_path, 'r:*') as source:
        members = source.getmembers()
        directories = {relative(member.name): member for member in members if member.isdir()}
        emitted = set()
        with tarfile.open(destination, 'w', format=tarfile.PAX_FORMAT) as output:
            def directory(name):
                if name in emitted:
                    return
                if name != '.':
                    directory(str(PurePosixPath(name).parent))
                member = directories.get(name)
                if member is None:
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o755
                    member.uid = member.gid = member.mtime = 0
                output.addfile(member)
                emitted.add(name)

            for member in members:
                name = relative(member.name)
                if member.isdir():
                    directory(name)
                else:
                    directory(str(PurePosixPath(name).parent))
                    stream = source.extractfile(member) if member.isfile() else None
                    try:
                        output.addfile(member, stream)
                    finally:
                        if stream is not None:
                            stream.close()


def summarize(data, architecture):
    md5sums = []
    size = 0
    seen = set()
    with tarfile.open(data, 'r:*') as source:
        entries = {relative(member.name): member for member in source.getmembers()}
        for name, member in entries.items():
            inode = name
            visited = set()
            while entries[inode].islnk():
                require(inode not in visited, 'hardlink cycle: ' + name)
                visited.add(inode)
                inode = relative(entries[inode].linkname)
                require(inode in entries, 'missing hardlink target: ' + name)
            actual = entries[inode]
            if inode not in seen:
                if actual.isfile():
                    size += math.ceil(actual.size / 1024)
                elif actual.issym():
                    size += math.ceil(len(actual.linkname.encode()) / 1024)
                else:
                    size += 1
                seen.add(inode)
            if member.isfile() or member.islnk():
                stream = source.extractfile(member)
                require(stream is not None, 'missing file bytes: ' + name)
                with stream:
                    first = stream.read(20)
                    if first.startswith(b'\x7fELF'):
                        require(len(first) == 20 and first[5] in (1, 2), 'invalid ELF header: ' + name)
                        machine = struct.unpack(('<' if first[5] == 1 else '>') + 'H', first[18:20])[0]
                        require(architecture != 'all', 'Architecture: all contains target ELF: ' + name)
                        require(machine == ARCHITECTURES[architecture], 'ELF architecture mismatch: ' + name)
                    digest = hashlib.md5(first)
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                    md5sums.append((name, digest.hexdigest()))
    return entries, size, ''.join(digest + '  ' + name + '\n' for name, digest in sorted(md5sums))


def assemble(args):
    fields = fields_from(args.control) if args.control else Deb822(dict(item.split('=', 1) for item in args.field))
    if not args.control:
        # The original scalar API permits omitted or empty descriptive fields.
        fields.setdefault('Maintainer', '')
        fields.setdefault('Description', '')
    required_fields = ('Package', 'Version', 'Architecture')
    if args.control:
        required_fields += ('Maintainer', 'Description')
    for name in required_fields:
        require(fields.get(name), 'control lacks required field: ' + name)
    require(re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', fields['Package']), 'invalid Package')
    Version(fields['Version'])
    require(not any(c in fields['Version'] for c in '/\\\n\r'), 'invalid Version')
    require(fields['Architecture'] in (*ARCHITECTURES, 'all'), 'unsupported Architecture')
    require(fields['Architecture'] in (args.target_architecture, 'all'), 'control architecture differs from target platform')
    require(not any('${' in str(value) for value in fields.values()), 'unresolved substitution in finalized control')
    control_files = {}
    for item in args.control_file:
        name, path = item.split('=', 1)
        require(name and name not in RESERVED and '/' not in name and '\\' not in name and name not in ('.', '..'), 'reserved or invalid control member: ' + name)
        require(name not in control_files, 'duplicate control member: ' + name)
        control_files[name] = Path(path)
    destination = args.output or args.output_dir
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sonic-deb-', dir=destination.parent) as temporary:
        root = Path(temporary)
        data = root / 'data.tar'
        raw_data = root / 'unordered-data.tar'
        subprocess.run([str(args.tar), '-cf', str(raw_data), '--format=pax', '--numeric-owner', '@' + str(args.data)], check=True)
        order_payload(raw_data, data)
        entries, installed_size, sums = summarize(data, fields['Architecture'])
        if 'conffiles' in control_files:
            for line in control_files['conffiles'].read_text().splitlines():
                path = line.strip()
                if not path:
                    continue
                require(path.startswith('/'), 'conffiles paths must be absolute: ' + path)
                name = relative(path[1:])
                require(name in entries and entries[name].isfile(), 'conffile absent from payload: ' + path)
        fields['Installed-Size'] = str(installed_size)
        control = root / 'control'
        control.mkdir()
        (control / 'control').write_text(fields.dump())
        (control / 'md5sums').write_text(sums)
        for name, source in control_files.items():
            (control / name).write_bytes(source.read_bytes())
        for item in control.iterdir():
            item.chmod(0o755 if item.name in SCRIPTS else 0o644)
            os.utime(item, (0, 0))
        os.utime(control, (0, 0))
        # The control archive has Debian-defined ownership; payload owners stay intact.
        subprocess.run([str(args.tar), '-czf', str(root / 'control.tar.gz'), '--format=pax',
                        '--uid=0', '--gid=0', '--numeric-owner', '-C', str(control), '.'], check=True)
        subprocess.run([str(args.tar), '-czf', str(root / 'data.tar.gz'), '--format=pax', '--numeric-owner', '@' + str(data)], check=True)
        (root / 'debian-binary').write_bytes(b'2.0\n')
        filename = '{}_{}_{}.deb'.format(fields['Package'], fields['Version'], fields['Architecture'])
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            result = args.output_dir / filename
        else:
            result = args.output
            require(result.name == filename, 'declared output name differs from binary control')
        subprocess.run([str(args.ar), 'rcD', str(result), str(root / 'debian-binary'),
                        str(root / 'control.tar.gz'), str(root / 'data.tar.gz')], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--control', type=Path)
    parser.add_argument('--control-file', action='append', default=[])
    parser.add_argument('--field', action='append', default=[])
    parser.add_argument('--target-architecture', choices=ARCHITECTURES, required=True)
    parser.add_argument('--tar', type=Path, required=True)
    parser.add_argument('--ar', type=Path, required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument('--output', type=Path)
    output.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    args.tar = args.tar.resolve()
    args.ar = args.ar.resolve()
    args.data = args.data.resolve()
    if args.control:
        require(not args.field, 'control and scalar fields are mutually exclusive')
    try:
        assemble(args)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
