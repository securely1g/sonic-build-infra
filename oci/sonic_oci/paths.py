"""Preserve the reviewed normalization of source package paths and metadata."""

import copy
from pathlib import PurePosixPath

def require(value, message):
    if not value:
        raise ValueError(message)

DIRECTORY_ALIASES = {name: {'linkname': target, 'target': target} for name, target in {
    'bin': 'usr/bin', 'lib': 'usr/lib', 'lib64': 'usr/lib64', 'sbin': 'usr/sbin',
}.items()}

DIRECTORY_ALIASES['var/run'] = {'linkname': '/run', 'target': 'run'}

def path_name(value):
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts, 'unsafe package payload path: ' + value)
    return str(path)

def normalized_path(value, *, directory_aliases=DIRECTORY_ALIASES):
    """Rewrite an image-relative path without following any build-host links."""
    name = path_name(value)
    for alias in sorted(directory_aliases, key=len, reverse=True):
        entry = directory_aliases[alias]
        if name == alias or name.startswith(alias + '/'):
            return entry['target'] + name[len(alias):]
    return name

def normalized_member(member, *, directory_aliases=DIRECTORY_ALIASES, root_owned=False, modes=None):
    """Copy one supported member, preserving imported owners unless explicitly changed."""
    name = path_name(member.name)
    require(not PurePosixPath(name).name.startswith('.wh.'), 'package payload uses a reserved OCI whiteout path: ' + name)
    require(member.isfile() or member.isdir() or member.issym() or member.islnk(),
            'unsupported package payload member: ' + name)
    require(member.sparse is None, 'sparse package payload member requires review: ' + name)
    output = copy.copy(member)
    output.pax_headers = dict(member.pax_headers)
    if root_owned:
        output.uid = output.gid = 0
        output.uname = output.gname = ''
        for field in ('uid', 'gid', 'uname', 'gname'):
            output.pax_headers.pop(field, None)
        if output.issym():
            output.mode = 0o777
    if name in directory_aliases:
        require(output.isdir() or (output.issym() and output.linkname == directory_aliases[name]['linkname']),
                'package payload changes a directory alias: ' + name)
        require(output.uid == 0 and output.gid == 0 and output.mode == (0o755 if output.isdir() else 0o777),
                'package payload changes directory alias metadata: ' + name)
        return None
    normalized = normalized_path(member.name, directory_aliases=directory_aliases)
    output.name = './' + normalized if normalized != '.' else './'
    output.pax_headers.pop('path', None)
    output.pax_headers.pop('linkpath', None)
    if member.islnk():
        target = path_name(member.linkname)
        require(target not in directory_aliases, 'package hardlink targets a directory alias: ' + name)
        output.linkname = './' + normalized_path(member.linkname, directory_aliases=directory_aliases)
    if modes and normalized in modes:
        value = modes[normalized]
        output.mode = int(value, 8) if isinstance(value, str) else value
        require(isinstance(output.mode, int) and 0 <= output.mode <= 0o7777, 'invalid mode for ' + normalized)
    return output
