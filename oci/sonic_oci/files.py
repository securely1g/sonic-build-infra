"""Read selected regular files from ordered OCI layers, including whiteouts."""

from pathlib import PurePosixPath
import tarfile


def read_files(layers, names):
    result = {}
    for layer in layers:
        additions, removals = {}, set()
        with tarfile.open(layer, "r:*") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("unsafe OCI layer path: " + member.name)
                name = str(path)
                if name in names:
                    if not member.isfile():
                        raise ValueError("expected a regular OCI file: " + name)
                    additions[name] = (archive.extractfile(member).read(), member)
                elif path.name == ".wh..wh..opq":
                    parent = str(path.parent)
                    removals.update(n for n in names if parent == "." or n.startswith(parent + "/"))
                elif path.name.startswith(".wh."):
                    hidden = str(path.parent / path.name[4:])
                    removals.update(n for n in names if n == hidden or n.startswith(hidden + "/"))
        for name in removals:
            result.pop(name, None)
        result.update(additions)
    return result
