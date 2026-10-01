"""Run protoc from its declared Debian payloads and matching runtime."""

import os
import posixpath
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from python.runfiles import runfiles


ARCHITECTURE = @ARCHITECTURE@
PAYLOADS = @PAYLOADS@
LOADERS = {
    "amd64": "ld-linux-x86-64.so.2",
    "arm64": "ld-linux-aarch64.so.1",
}


def extract_payload(path: Path, root: Path) -> None:
    with tarfile.open(path, mode="r:*") as archive:
        members = []
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"Invalid package path: {member.name}")
            if member.issym() and member.linkname.startswith("/"):
                member.linkname = posixpath.relpath(
                    member.linkname.lstrip("/"),
                    posixpath.dirname(member.name),
                )
            members.append(member)
        archive.extractall(root, members=members, filter="data")


def executable(root: Path) -> tuple[Path, Path, str]:
    compiler = root / "usr/bin/protoc"
    if not compiler.is_file():
        raise ValueError("Declared protobuf payloads do not contain usr/bin/protoc")
    loaders = sorted({path.resolve() for path in root.rglob(LOADERS[ARCHITECTURE]) if path.is_file()})
    if len(loaders) != 1 or not loaders[0].is_relative_to(root):
        raise ValueError(f"Expected one declared {ARCHITECTURE} loader, found {loaders}")
    directories = sorted({path.parent.resolve() for path in root.rglob("*.so*") if path.is_file()})
    if not directories or any(not path.is_relative_to(root) for path in directories):
        raise ValueError("Declared protobuf library directories escape the package root")
    return compiler, loaders[0], ":".join(map(str, directories))


def main() -> int:
    resolver = runfiles.Create()
    if resolver is None:
        raise RuntimeError("Bazel runfiles are unavailable")
    with tempfile.TemporaryDirectory(prefix="protobuf-debian-") as directory:
        root = Path(directory).resolve()
        for logical_path in PAYLOADS:
            resolved = resolver.Rlocation(logical_path)
            if not resolved:
                raise ValueError(f"Missing declared protobuf payload: {logical_path}")
            extract_payload(Path(resolved), root)
        compiler, loader, library_path = executable(root)
        command = [str(loader), "--inhibit-cache", "--library-path", library_path]
        environment = {"HOME": directory, "LANG": "C", "LC_ALL": "C", "PATH": "", "TMPDIR": directory}
        listing = subprocess.run(command + ["--list", str(compiler)], env=environment, text=True, capture_output=True, check=True)
        if "not found" in listing.stdout:
            raise RuntimeError("Declared protoc runtime is incomplete:\n" + listing.stdout)
        for line in listing.stdout.splitlines():
            # The loader can print its original ELF interpreter path on the
            # left of =>. Validate the resolved file on the right instead.
            fields = line.split("=>", 1)[-1].strip().split()
            if fields and fields[0].startswith("/"):
                value = fields[0]
                if not Path(value).resolve().is_relative_to(root):
                    raise RuntimeError(f"protoc selected an undeclared runtime library: {value}")
        invocation = command + [str(compiler)]
        version = subprocess.check_output(invocation + ["--version"], env=environment, text=True).strip()
        if version != "libprotoc 3.21.12":
            raise RuntimeError(f"Unexpected protoc version: {version}")
        return subprocess.run(
            invocation + ["--proto_path=" + str(root / "usr/include")] + sys.argv[1:],
            env=environment,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
