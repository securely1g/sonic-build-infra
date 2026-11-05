"""Extract a declared SWIG subtree, with optional complete-tree validation."""
import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile


def extract(archives, prefix, output, *, require_entry_point=False):
    prefix = PurePosixPath(prefix)
    if prefix.is_absolute() or '..' in prefix.parts or str(prefix) in ('', '.'):
        raise ValueError('SWIG template prefix must be a nonempty relative directory')
    output.mkdir(parents=True, exist_ok=True)
    links = []
    for archive in archives:
        with tarfile.open(archive, 'r:*') as source:
            for member in source:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('unsafe SWIG archive path: ' + member.name)
                if not path.is_relative_to(prefix):
                    continue
                relative = path.relative_to(prefix)
                if str(relative) == '.':
                    continue
                destination = output / str(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    destination.mkdir(exist_ok=True)
                elif member.isfile():
                    stream = source.extractfile(member)
                    if stream is None:
                        raise ValueError('missing SWIG template data: ' + member.name)
                    with stream, destination.open('wb') as target:
                        shutil.copyfileobj(stream, target)
                    destination.chmod(member.mode & 0o777)
                elif member.issym() or member.islnk():
                    links.append((destination, member.linkname, member.islnk()))
                else:
                    raise ValueError('unsupported SWIG template member: ' + member.name)
    root = output.resolve()
    for destination, name, hardlink in links:
        target = (output / str(PurePosixPath(name).relative_to(prefix))) if hardlink else destination.parent / name
        if not target.resolve().is_relative_to(root):
            raise ValueError('SWIG template link escapes declared subtree: ' + name)
        if hardlink:
            os.link(target, destination)
        else:
            destination.symlink_to(name)
    if require_entry_point and not (output / 'swig.swg').is_file():
        raise ValueError('SWIG template input lacks required swig.swg under ' + str(prefix))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, action='append', required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-entry-point', action='store_true')
    args = parser.parse_args()
    try:
        extract(args.archive, args.prefix, args.output, require_entry_point=args.require_entry_point)
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
