"""Run a native Linux ELF using the matching loader from declared target data."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import signal
import tarfile
import tempfile
import zlib

import elf_metadata
from elf_metadata import Elf


def origin_paths(elf, directory):
    paths = {15: [], 29: []}
    has_runpath = False
    for section in elf.sections:
        if section[1] != 6:
            continue
        strings = elf.section_data(elf.sections[section[6]])
        for offset in range(section[4], section[4] + section[5], section[9]):
            tag, value = elf.unpack("qQ" if elf.bits == 64 else "iI", offset)
            if tag not in paths:
                continue
            has_runpath |= tag == 29
            for entry in elf.string(strings, value).split(":"):
                if "$ORIGIN" in entry or "${ORIGIN}" in entry:
                    paths[tag].append(entry.replace("${ORIGIN}", str(directory)).replace("$ORIGIN", str(directory)))
    return ([] if has_runpath else paths[15], paths[29], has_runpath)


def run(binary, payloads, arguments, instrument=None, instrument_arguments=(), *,
        target_machine=None, environment=None):
    binary = Path(binary).resolve()
    elf = Elf(binary.read_bytes())
    expected = target_machine if target_machine is not None else {"x86_64": 62, "aarch64": 183}.get(platform.machine())
    if expected not in (62, 183) or elf.machine != expected:
        raise ValueError("runtime binary does not match the declared target CPU")
    with tempfile.TemporaryDirectory(prefix="native-target-runtime-") as temporary:
        root = Path(temporary)
        libraries = {}
        library_metadata = {}
        debug_files = {}
        loader = None
        for payload in payloads:
            with tarfile.open(payload) as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    contents = archive.extractfile(member).read()
                    if contents[:4] != b"\x7fELF":
                        continue
                    item = Elf(contents)
                    if item.machine != expected:
                        raise ValueError("runtime payload contains an ELF for a different CPU: " + member.name)
                    if member.name.removeprefix("./").startswith("usr/lib/debug/") and item.build_id:
                        previous = debug_files.setdefault(item.build_id, contents)
                        if previous != contents:
                            raise ValueError("conflicting runtime debug Build-ID: " + item.build_id)
                        continue
                    if not item.soname:
                        continue
                    digest = hashlib.sha256(contents).digest()
                    if item.soname in libraries:
                        if libraries[item.soname] != digest:
                            raise ValueError("conflicting runtime SONAME: " + item.soname)
                        continue
                    if Path(item.soname).name != item.soname:
                        raise ValueError("invalid runtime SONAME")
                    libraries[item.soname] = digest
                    library_metadata[item.soname] = item
                    target = root / item.soname
                    target.write_bytes(contents)
                    target.chmod(0o755)
                    if item.soname.startswith("ld-linux-"):
                        if loader is not None:
                            raise ValueError("multiple target ELF loaders")
                        loader = target
        if loader is None or "libc.so.6" not in libraries:
            raise ValueError("runtime_payloads must include a matching libc6 loader and libc.so.6")
        for item in library_metadata.values():
            debug = debug_files.get(item.build_id)
            if debug is None or not item.debuglink:
                continue
            if Path(item.debuglink).name != item.debuglink:
                raise ValueError("invalid runtime debuglink")
            if zlib.crc32(debug) != item.debuglink_crc:
                raise ValueError("runtime debuglink CRC differs from matching Build-ID data")
            destination = root / ".debug" / item.debuglink
            destination.parent.mkdir(exist_ok=True)
            if destination.exists() and destination.read_bytes() != debug:
                raise ValueError("conflicting runtime debuglink filename: " + item.debuglink)
            destination.write_bytes(debug)
        pending = [(binary, [])]
        checked = set()
        while pending:
            current, inherited_rpaths = pending.pop()
            if current in checked:
                continue
            checked.add(current)
            current_elf = Elf(current.read_bytes())
            rpaths, runpaths, has_runpath = origin_paths(current_elf, current.parent)
            paths = rpaths + ([] if has_runpath else inherited_rpaths) + [str(root)] + runpaths
            for soname in current_elf.needed:
                provider = next((Path(directory) / soname for directory in paths if (Path(directory) / soname).is_file()), None)
                if provider is None:
                    raise ValueError("undeclared runtime library: " + soname + " required by " + str(current))
                pending.append((provider, rpaths + inherited_rpaths))
        environment = dict(os.environ if environment is None else environment)
        for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT"):
            environment.pop(name, None)
        # Let the loader apply each object's RPATH/RUNPATH itself. Promoting
        # the main RUNPATH to --library-path incorrectly makes it transitive.
        command = [str(loader), "--inhibit-cache", "--library-path", str(root), str(binary)] + arguments
        if instrument:
            command = [str(Path(instrument).absolute())] + list(instrument_arguments) + command
        # Preserve the caller directory while avoiding posix_spawn EFAULT with
        # large environments when native Python executes under PRoot.
        return subprocess.run(command, env=environment, cwd=os.getcwd()).returncode


def prepare_native(python, binary, payloads, arguments, target_machine,
                   instrument=None, instrument_arguments=()):
    """Run CPU-independent preparation with a declared native private Python.

    The public build-tool wrapper needs its own PYTHONHOME, so it cannot use
    -E. Its startup receives a sanitized environment; the target later gets
    the caller's exact environment, subject only to run()'s loader filtering.
    An inherited unlinked file keeps this snapshot out of argv and named files.
    """
    if target_machine not in (62, 183):
        raise ValueError("native preparation requires an explicit target CPU")
    original = dict(os.environ)
    startup = {name: value for name, value in original.items()
               if not name.startswith(("PYTHON", "LD_"))}
    startup.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONSAFEPATH="1")
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]=sys.argv.pop(1);runpy.run_path(sys.argv[0],run_name='__main__')"
    # A caller may have closed a standard descriptor. Move the transport out
    # of that range and close the original before starting the child.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as temporary:
        descriptor = fcntl.fcntl(temporary.fileno(), fcntl.F_DUPFD_CLOEXEC, 3)
    with os.fdopen(descriptor, mode="w+", encoding="utf-8") as stream:
        if os.fstat(descriptor).st_nlink != 0:
            raise RuntimeError("caller environment transport must be unlinked before writing")
        json.dump(original, stream, ensure_ascii=True)
        stream.flush()
        stream.seek(0)
        command = [str(python), "-B", "-S", "-c", bootstrap,
                   str(Path(elf_metadata.__file__).resolve().parent), str(Path(__file__).resolve()),
                   "--caller-environment-fd", str(descriptor),
                   "--target-machine", str(target_machine), "--binary", str(binary)]
        for payload in payloads:
            command += ["--payload", str(payload)]
        if instrument:
            command += ["--instrument", str(instrument)]
        command += ["--instrument-arg=" + value for value in instrument_arguments]
        command += ["--", *arguments]
        return subprocess.run(command, env=startup, pass_fds=(descriptor,)).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--payload", action="append", default=[])
    parser.add_argument("--instrument")
    parser.add_argument("--instrument-arg", action="append", default=[])
    parser.add_argument("--target-machine", type=int, choices=(62, 183))
    parser.add_argument("--preparation-python")
    parser.add_argument("--caller-environment-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    arguments = options.arguments[1:] if options.arguments[:1] == ["--"] else options.arguments
    if options.preparation_python:
        if options.caller_environment_fd is not None or options.target_machine is None:
            parser.error("native preparation requires an explicit target CPU and no inherited environment descriptor")
        return prepare_native(options.preparation_python, options.binary, options.payload, arguments,
                              options.target_machine, options.instrument, options.instrument_arg)
    environment = None
    if options.caller_environment_fd is not None:
        if options.caller_environment_fd < 3 or options.target_machine is None:
            parser.error("caller environment requires a private descriptor and an explicit target CPU")
        with os.fdopen(options.caller_environment_fd, encoding="utf-8") as stream:
            environment = json.load(stream)
        if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                    for k, v in environment.items()):
            parser.error("invalid caller environment snapshot")
    return run(options.binary, options.payload, arguments, options.instrument, options.instrument_arg,
               target_machine=options.target_machine, environment=environment)


if __name__ == "__main__":
    status = main()
    if status < 0:
        terminating_signal = -status
        if terminating_signal not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(terminating_signal, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {terminating_signal})
        os.kill(os.getpid(), terminating_signal)
    raise SystemExit(status)
