"""Inspect flat, explicit mtree payloads directly from declared file sources."""
import hashlib
import math
from pathlib import Path, PurePosixPath
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def relative(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts, 'unsafe data path: ' + name)
    return str(path)


def mtree_unescape(value):
    return re.sub(rb'\\([0-7]{3})', lambda match: bytes([int(match[1], 8)]), value.encode()).decode()


def summarize_mtree(data, architecture):
    """Inspect file sources directly; no temporary payload tree or TAR is needed."""
    entries, sums, size = {}, [], 0
    for line in data.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        words = line.split()
        name = relative(mtree_unescape(words[0]))
        require(name not in entries, 'duplicate mtree path: ' + name)
        fields = dict(word.split('=', 1) for word in words[1:])
        kind = fields['type']
        require(kind in ('file', 'dir', 'link'), 'unsupported mtree entry type: ' + kind)
        entries[name] = fields
        if kind == 'file':
            source = Path(mtree_unescape(fields.get('contents', fields.get('content', ''))))
            require(source.is_file(), 'missing mtree content: ' + name)
            size += math.ceil(source.stat().st_size / 1024)
            with source.open('rb') as stream:
                first = stream.read(20)
                if first.startswith(b'\x7fELF'):
                    require(len(first) == 20 and first[5] in (1, 2), 'invalid ELF header: ' + name)
                    require(architecture != 'all', 'Architecture: all contains target ELF: ' + name)
                digest = hashlib.md5(first)
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            sums.append((name, digest.hexdigest()))
        elif kind == 'link':
            size += math.ceil(len(mtree_unescape(fields['link']).encode()) / 1024)
        else:
            size += 1
    for name in entries:
        if name != '.':
            parent = str(PurePosixPath(name).parent)
            require(parent in entries and entries[parent]['type'] == 'dir', 'missing mtree parent: ' + parent)
    return entries, size, ''.join(digest + '  ' + name + '\n' for name, digest in sorted(sums))
