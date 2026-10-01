"""Run the pinned dpkg-deb and its helpers with their declared Debian runtime."""

import posixpath
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from python.runfiles import runfiles


ARCHITECTURE = @ARCHITECTURE@
PAYLOADS = @PAYLOADS@
DPKG_VERSION = "1.22.22"
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
                raise ValueError(f"Invalid tool package path: {member.name}")
            if member.issym() and member.linkname.startswith("/"):
                member.linkname = posixpath.relpath(
                    member.linkname.lstrip("/"),
                    posixpath.dirname(member.name),
                )
            members.append(member)
        archive.extractall(root, members=members, filter="data")


def runtime(root: Path) -> list[str]:
    loaders = sorted({path.resolve() for path in root.rglob(LOADERS[ARCHITECTURE]) if path.is_file()})
    if len(loaders) != 1 or not loaders[0].is_relative_to(root):
        raise ValueError(f"Expected one declared {ARCHITECTURE} loader, found {loaders}")
    directories = sorted({path.parent.resolve() for path in root.rglob("*.so*") if path.is_file()})
    if not directories or any(not path.is_relative_to(root) for path in directories):
        raise ValueError("Declared dpkg library directories escape the package root")
    return [str(loaders[0]), "--inhibit-cache", "--library-path", ":".join(map(str, directories))]


def check_runtime(root: Path, loader: list[str], program: Path, environment: dict[str, str]) -> None:
    if not program.is_file() or not program.resolve().is_relative_to(root):
        raise ValueError(f"Missing declared dpkg tool: {program}")
    listing = subprocess.run(
        loader + ["--list", str(program)],
        env=environment, text=True, capture_output=True, check=True,
    )
    if "not found" in listing.stdout:
        raise RuntimeError(f"Incomplete runtime for {program}:\n{listing.stdout}")
    for line in listing.stdout.splitlines():
        # The interpreter's original ELF pathname may be printed left of =>;
        # the resolved library is on the right. Reject host-library fallback.
        fields = line.split("=>", 1)[-1].strip().split()
        if fields and fields[0].startswith("/"):
            if not Path(fields[0]).resolve().is_relative_to(root):
                raise RuntimeError(f"{program.name} selected an undeclared runtime: {fields[0]}")


def helper_launcher(path: Path, python: Path, loader: list[str], program: Path) -> None:
    # dpkg-deb invokes tar through PATH and rm when cleaning up --info/--field.
    # Each helper must use the declared loader too; invoking its ELF directly
    # would use the host's interpreter. python points to Bazel's Python tool.
    invocation = loader + [str(program)]
    path.write_text(
        f"#!{python}\n"
        "import os, sys\n"
        f"command = {invocation!r} + sys.argv[1:]\n"
        "os.execve(command[0], command, os.environ)\n"
    )
    path.chmod(0o755)


def own_runfiles():
    # A tool may be launched by another py_binary, which leaves its own
    # RUNFILES_DIR in the environment. Prefer this binary's enclosing tree.
    for directory in Path(__file__).absolute().parents:
        if directory.name.endswith(".runfiles"):
            return runfiles.Create({"RUNFILES_DIR": str(directory)})
    return runfiles.Create()


def main() -> int:
    resolver = own_runfiles()
    if resolver is None:
        raise RuntimeError("Bazel runfiles are unavailable")
    # A Bazel sandbox/runfiles path may exceed the Linux shebang length limit.
    # Keep the interpreter symlink used by our generated helpers under /tmp.
    with tempfile.TemporaryDirectory(prefix="dpkg-debian-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        for logical_path in PAYLOADS:
            resolved = resolver.Rlocation(logical_path)
            if not resolved:
                raise ValueError(f"Missing declared dpkg payload: {logical_path}")
            extract_payload(Path(resolved), root)
        loader = runtime(root)
        helpers = root / "helpers"
        helpers.mkdir()
        python = helpers / "python"
        python.symlink_to(Path(sys.executable).resolve())
        environment = {
            "HOME": directory,
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": str(helpers),
            "TMPDIR": directory,
            "TZ": "UTC",
            "SOURCE_DATE_EPOCH": "0",
            "DPKG_DEB_THREADS_MAX": "1",
        }
        binary = root / "usr/bin/dpkg-deb"
        check_runtime(root, loader, binary, environment)
        for name in ["tar", "rm"]:
            program = root / "usr/bin" / name
            check_runtime(root, loader, program, environment)
            helper_launcher(helpers / name, python, loader, program)
        invocation = loader + [str(binary)]
        version = subprocess.check_output(invocation + ["--version"], env=environment, text=True).splitlines()[0]
        expected = f"Debian 'dpkg-deb' package archive backend version {DPKG_VERSION} ({ARCHITECTURE})."
        if version != expected:
            raise RuntimeError(f"Unexpected dpkg-deb version: {version}")
        return subprocess.run(invocation + sys.argv[1:], env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
