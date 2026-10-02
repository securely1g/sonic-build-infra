#!/usr/bin/env python3
"""Install wheels with PyPA installer and create an owned, deterministic tar."""

import argparse
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
import zipfile

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.sources import WheelFile


def absolute_directory(path):
    parsed = PurePosixPath(path)
    if not parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
        raise ValueError(f"Expected an absolute target path without traversal: {path}")
    return str(parsed)


def validate_paths(wheel):
    with zipfile.ZipFile(wheel) as archive:
        seen = set()
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                raise ValueError(f"Invalid wheel member path: {entry.filename}")
            if path in seen:
                raise ValueError(f"Duplicate wheel member path: {entry.filename}")
            seen.add(path)


class LayerDestination(SchemeDictionaryDestination):
    def write_to_fs(self, scheme, path, stream, is_executable):
        # Entry-point names also produce files but do not occur in the ZIP member
        # list. Validate every destination path, including those generated names.
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in path:
            raise ValueError(f"Invalid wheel installation path: {path}")
        return super().write_to_fs(scheme, path, stream, is_executable)


def build_layer(wheels, output, site_packages, scripts, headers, data, interpreter):
    scheme = {
        "purelib": absolute_directory(site_packages),
        "platlib": absolute_directory(site_packages),
        "scripts": absolute_directory(scripts),
        "headers": absolute_directory(headers),
        "data": absolute_directory(data),
    }
    absolute_directory(interpreter)
    # installer creates normal files/directories using the process umask. Fix it
    # for reproducibility; executable wheel entries and launchers stay executable.
    previous_umask = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory() as directory:
            destination = LayerDestination(
                scheme_dict=scheme,
                interpreter=interpreter,
                script_kind="posix",
                bytecode_optimization_levels=(),
                destdir=directory,
            )
            for wheel in wheels:
                validate_paths(wheel)
                with WheelFile.open(wheel) as source:
                    source.validate_record(validate_contents=True)
                    install(source, destination, additional_metadata={"INSTALLER": b"sonic-build-infra\n"})
            root = Path(directory)
            with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(root.rglob("*")):
                    member = archive.gettarinfo(str(path), arcname="./" + path.relative_to(root).as_posix())
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    member.mtime = 0
                    if member.isfile():
                        with path.open("rb") as contents:
                            archive.addfile(member, contents)
                    else:
                        archive.addfile(member)
    finally:
        os.umask(previous_umask)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site-packages", required=True)
    parser.add_argument("--scripts", default="/usr/local/bin")
    parser.add_argument("--headers", default="/usr/local/include")
    parser.add_argument("--data", default="/usr/local")
    parser.add_argument("--interpreter", default="/usr/bin/python3")
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args()
    build_layer(args.wheels, args.output, args.site_packages, args.scripts, args.headers, args.data, args.interpreter)


if __name__ == "__main__":
    main()
