"""Run a native Linux ELF using the matching loader from declared target data."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import signal
import tarfile
import tempfile
import zlib

from elf_metadata import Elf


def run(binary, payloads, arguments, instrument=None, instrument_arguments=()):
    binary = Path(binary).resolve()
    elf = Elf(binary.read_bytes())
    native = {"x86_64": 62, "aarch64": 183}.get(platform.machine())
    if elf.machine != native:
        raise ValueError("native_runtime_test requires matching execution and target CPUs")
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
                    if item.machine != native:
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
        paths = [str(root)]
        for section in elf.sections:
            if section[1] != 6:
                continue
            strings = elf.section_data(elf.sections[section[6]])
            for offset in range(section[4], section[4] + section[5], section[9]):
                tag, value = elf.unpack("qQ" if elf.bits == 64 else "iI", offset)
                if tag in (15, 29):
                    for entry in elf.string(strings, value).split(":"):
                        if "$ORIGIN" in entry or "${ORIGIN}" in entry:
                            paths.append(entry.replace("${ORIGIN}", str(binary.parent)).replace("$ORIGIN", str(binary.parent)))
        pending = [binary]
        checked = set()
        while pending:
            current = pending.pop()
            if current in checked:
                continue
            checked.add(current)
            for soname in Elf(current.read_bytes()).needed:
                provider = next((Path(directory) / soname for directory in paths if (Path(directory) / soname).is_file()), None)
                if provider is None:
                    raise ValueError("undeclared runtime library: " + soname + " required by " + str(current))
                pending.append(provider)
        environment = dict(os.environ)
        for name in ("LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT"):
            environment.pop(name, None)
        command = [str(loader), "--inhibit-cache", "--library-path", ":".join(paths), str(binary)] + arguments
        if instrument:
            command = [str(Path(instrument).absolute())] + list(instrument_arguments) + command
        return subprocess.run(command, env=environment).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--payload", action="append", default=[])
    parser.add_argument("--instrument")
    parser.add_argument("--instrument-arg", action="append", default=[])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    arguments = options.arguments[1:] if options.arguments[:1] == ["--"] else options.arguments
    return run(options.binary, options.payload, arguments, options.instrument, options.instrument_arg)


if __name__ == "__main__":
    status = main()
    if status < 0:
        terminating_signal = -status
        if terminating_signal not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(terminating_signal, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {terminating_signal})
        os.kill(os.getpid(), terminating_signal)
    raise SystemExit(status)
