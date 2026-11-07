"""Bounded source/transport tests; synthetic ELF data is never executed.

The transport cases use the declared build_tool launcher source and this
test's declared interpreter as a runtime stand-in, plus a child observer for
descriptor handoff. They do not replace the real native/ARM runtime tests.
"""
import ast
import io
import json
import os
from pathlib import Path
import signal
import shlex
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cc import runtime_test as runtime
from elf_metadata import Elf


# Bazel supplies these source files explicitly in args/data. Capture the test
# interpreter before the transport cases replace the caller environment.
RUNTIME_BZL = Path(sys.argv.pop(1)).resolve()
TOOL_LAUNCHER = Path(sys.argv.pop(1)).resolve()
TEST_PYTHON = Path(sys.executable).resolve()
TEST_PREFIX = sys.prefix


def elf(machine, soname=None, needed=(), suffix=b""):
    """Construct real parseable ELF64 metadata, without executable code."""
    names = b"\0.shstrtab\0.dynstr\0.dynamic\0"
    strings = b"\0"
    dynamic = []
    for tag, value in [(1, n) for n in needed] + ([(14, soname)] if soname else []):
        dynamic.append((tag, len(strings)))
        strings += value.encode() + b"\0"
    data = b"".join(struct.pack("<qQ", *item) for item in dynamic + [(0, 0)])
    offset = 64 + 4 * 64
    sections = [(0,) * 10,
                (1, 3, 0, 0, offset, len(names), 0, 0, 1, 0),
                (11, 3, 0, 0, offset + len(names), len(strings), 0, 0, 1, 0),
                (19, 6, 0, 0, offset + len(names) + len(strings), len(data), 2, 0, 8, 16)]
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    header = struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, 0, 64, 0, 64, 0, 0, 64, 4, 1)
    return ident + header + b"".join(struct.pack("<IIQQQQIIQQ", *s) for s in sections) + names + strings + data + suffix


class RuntimePreparationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="runtime-preparation-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / "target"
        self.binary.write_bytes(elf(183, needed=["libc.so.6"]))
        self.payload = self.archive("runtime.tar", [
            ("lib/libc.so.6", elf(183, "libc.so.6")),
            ("lib/ld-linux-aarch64.so.1", elf(183, "ld-linux-aarch64.so.1")),
        ])

    def archive(self, name, entries):
        destination = self.root / name
        with tarfile.open(destination, "w") as out:
            for path, data in entries:
                member = tarfile.TarInfo(path)
                member.size = len(data)
                out.addfile(member, io.BytesIO(data))
        return destination

    def test_explicit_arm_target_keeps_real_arm_loader_metadata(self):
        def observe(command, **kwargs):
            self.assertEqual(Elf(Path(command[0]).read_bytes()).machine, 183)
            self.assertEqual(Elf(Path(command[4]).read_bytes()).machine, 183)
            self.assertEqual(command[1:3], ["--inhibit-cache", "--library-path"])
            self.assertEqual(command[-2:], ["--meaning", "42"])
            return SimpleNamespace(returncode=0)
        with patch.object(runtime.subprocess, "run", side_effect=observe):
            self.assertEqual(runtime.run(self.binary, [self.payload], ["--meaning", "42"], target_machine=183), 0)

    def test_wrong_declared_target_rejected_before_execution(self):
        with patch.object(runtime.subprocess, "run") as child:
            with self.assertRaisesRegex(ValueError, "declared target CPU"):
                runtime.run(self.binary, [self.payload], [], target_machine=62)
        child.assert_not_called()

    def test_legacy_native_default_still_rejects_cross_binary(self):
        host = {"x86_64": 62, "aarch64": 183}[runtime.platform.machine()]
        self.binary.write_bytes(elf(183 if host == 62 else 62))
        with self.assertRaisesRegex(ValueError, "declared target CPU"):
            runtime.run(self.binary, [], [])

    def test_mixed_payload_cpu_is_rejected(self):
        other = self.archive("wrong.tar", [("lib/bad.so", elf(62, "bad.so"))])
        with self.assertRaisesRegex(ValueError, "different CPU"):
            runtime.run(self.binary, [self.payload, other], [], target_machine=183)

    def test_conflicting_soname_is_rejected(self):
        other = self.archive("conflict.tar", [("lib/libc.so.6", elf(183, "libc.so.6", suffix=b"different"))])
        with self.assertRaisesRegex(ValueError, "conflicting runtime SONAME"):
            runtime.run(self.binary, [self.payload, other], [], target_machine=183)

    def test_missing_declared_dependency_is_rejected(self):
        self.binary.write_bytes(elf(183, needed=["absent.so"]))
        with self.assertRaisesRegex(ValueError, "undeclared runtime library"):
            runtime.run(self.binary, [self.payload], [], target_machine=183)

    def test_target_environment_copy_does_not_mutate_input(self):
        original = {"PYTHONHOME": "", "EMPTY": "", "LD_PRELOAD": "discard", "LD_DEBUG": "keep"}
        with patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(returncode=17)) as child:
            self.assertEqual(runtime.run(self.binary, [self.payload], [], target_machine=183, environment=original), 17)
        self.assertEqual(child.call_args.kwargs["env"], {"PYTHONHOME": "", "EMPTY": "", "LD_DEBUG": "keep"})
        self.assertEqual(original["LD_PRELOAD"], "discard")

    def make_instrument(self, signal_exit=False):
        observer = self.root / "observe.py"
        observer.write_text('''import json, os, pathlib, stat, sys
output, *command = sys.argv[1:]
def anonymous(pid):
    result = []
    for fd in pathlib.Path('/proc/' + str(pid) + '/fd').iterdir():
        try: status = fd.stat()
        except FileNotFoundError: continue
        if int(fd.name) >= 3 and stat.S_ISREG(status.st_mode) and status.st_nlink == 0:
            result.append(int(fd.name))
    return result
names = ['PYTHONHOME', 'PYTHONPATH', 'PYTHONNOUSERSITE', 'PYTHONINSPECT', 'EMPTY', 'UNSET', 'CALLER_MARKER', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'LD_AUDIT', 'LD_DEBUG']
pathlib.Path(output).write_text(json.dumps({'environment': {k: os.environ[k] for k in names if k in os.environ}, 'big_length': len(os.environ.get('BIG', '')), 'descriptor_leaked': anonymous(os.getpid()), 'preparation_descriptor_leaked': anonymous(os.getppid()), 'command': command}))
'''+("os.kill(os.getpid(), 15)\n" if signal_exit else "raise SystemExit(23)\n"))
        wrapper = self.root / "instrument"
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(TEST_PYTHON)) + ' -I -S ' + shlex.quote(str(observer)) + ' "$@"\n')
        wrapper.chmod(0o755)
        return wrapper

    def make_python_tool(self):
        # Use the unchanged source build_tool launcher for this part of the
        # exec chain. Its runtime below is explicitly a protocol-test stand-in.
        directory = self.root / "private-runtime"
        (directory / "bin").mkdir(parents=True)
        (directory / "toolchain.json").write_text(json.dumps({"format_version": 1, "profile": "foreign", "tools": {"python3": "protocol-test-python"}}))
        tool = directory / "bin/python3"
        tool.write_text('#!/bin/sh\nPYTHONHOME=' + shlex.quote(TEST_PREFIX) + '\nPYTHONNOUSERSITE=1\nexport PYTHONHOME PYTHONNOUSERSITE\nexec ' + shlex.quote(str(TEST_PYTHON)) + ' "$@"\n')
        tool.chmod(0o755)
        public = self.root / "public-python3"
        public.write_text('#!/bin/sh\nexec ' + shlex.quote(str(TEST_PYTHON)) + ' ' + shlex.quote(str(TOOL_LAUNCHER)) + ' python3 ' + shlex.quote(str(directory)) + ' "$@"\n')
        public.chmod(0o755)
        return public

    def test_source_launcher_transport_preserves_unset_empty_and_poisoned_environment(self):
        tool = self.make_python_tool()
        instrument = self.make_instrument()
        poison = self.root / "poison"
        poison.mkdir()
        (poison / "json.py").write_text("raise RuntimeError('caller Python path was used')\n")
        for python_environment in ({}, {"PYTHONHOME": "", "PYTHONNOUSERSITE": ""},
                                   {"PYTHONHOME": "/target/python", "PYTHONNOUSERSITE": "0"}):
            with self.subTest(python_environment=python_environment):
                environment = {"PATH": "", "PYTHONPATH": str(poison), "PYTHONINSPECT": "1",
                               "EMPTY": "", "CALLER_MARKER": "environment-only-marker",
                               "LD_LIBRARY_PATH": "/missing", "LD_PRELOAD": "discard", "LD_AUDIT": "discard",
                               "LD_DEBUG": "", "BIG": "x" * 70000, **python_environment}
                output = self.root / "observed.json"
                with patch.dict(os.environ, environment, clear=True):
                    status = runtime.prepare_native(tool, self.binary, [self.payload],
                                                    ["--literal", "argument with spaces"], 183,
                                                    instrument, [str(output)])
                self.assertEqual(status, 23)
                result = json.loads(output.read_text())
                expected = {k: v for k, v in environment.items() if k not in ("PATH", "BIG", "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT")}
                self.assertEqual(result["environment"], expected)
                self.assertEqual(result["big_length"], 70000)
                self.assertFalse(result["descriptor_leaked"])
                self.assertFalse(result["preparation_descriptor_leaked"])
                self.assertNotIn("environment-only-marker", " ".join(result["command"]))
                self.assertEqual(result["command"][-2:], ["--literal", "argument with spaces"])
                self.assertNotIn("--caller-environment-fd", result["command"])

    def test_signal_result_crosses_preparation_process(self):
        tool = self.make_python_tool()
        instrument = self.make_instrument(signal_exit=True)
        before = set(os.listdir("/proc/self/fd"))
        with patch.dict(os.environ, {"PATH": ""}, clear=True):
            result = runtime.prepare_native(tool, self.binary, [self.payload], [], 183,
                                            instrument, [str(self.root / "signal.json")])
        self.assertEqual(result, -signal.SIGTERM)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)

    def test_transport_is_unlinked_before_write_and_readable_at_exact_passed_fd(self):
        real_dump = json.dump
        observed = []

        def write(value, stream, **kwargs):
            status = os.fstat(stream.fileno())
            self.assertTrue(stat.S_ISREG(status.st_mode))
            self.assertEqual(status.st_nlink, 0)
            self.assertEqual(status.st_size, 0)
            self.assertEqual(stream.encoding.lower(), "utf-8")
            observed.append(stream.fileno())
            return real_dump(value, stream, **kwargs)

        def execute(command, **kwargs):
            descriptor = int(command[command.index("--caller-environment-fd") + 1])
            self.assertEqual(kwargs["pass_fds"], (descriptor,))
            self.assertEqual(observed, [descriptor])
            self.assertFalse(os.get_inheritable(descriptor))
            with os.fdopen(os.dup(descriptor), encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), environment)
            self.assertNotIn(environment["TOKEN"], command)
            return SimpleNamespace(returncode=0)

        environment = {"TOKEN": "fixture-transport-only", "EMPTY": "", "UNICODE": "Főtanúsítvány", "BIG": "x" * 70000}
        with patch.dict(os.environ, environment, clear=True), patch.object(runtime.json, "dump", side_effect=write), patch.object(runtime.subprocess, "run", side_effect=execute):
            self.assertEqual(runtime.prepare_native("unused", self.binary, [], [], 183), 0)
        with self.assertRaises(OSError):
            os.fstat(observed[0])

    def test_transport_operates_when_memfd_api_is_absent(self):
        existed = hasattr(os, "memfd_create")
        original = getattr(os, "memfd_create", None)
        if existed:
            del os.memfd_create
        try:
            tool = self.make_python_tool()
            instrument = self.make_instrument()
            output = self.root / "without-memfd.json"
            with patch.dict(os.environ, {"PATH": "", "BIG": "x" * 70000}, clear=True):
                self.assertEqual(runtime.prepare_native(tool, self.binary, [self.payload], [], 183, instrument, [str(output)]), 23)
            self.assertEqual(json.loads(output.read_text())["big_length"], 70000)
        finally:
            if existed:
                os.memfd_create = original

    def test_closed_standard_descriptors_remain_closed_with_private_transport(self):
        observer = self.root / "observe-transport.py"
        observer.write_text('''import json, os, pathlib, sys
descriptor = int(sys.argv[sys.argv.index('--caller-environment-fd') + 1])
open_standard = []
for candidate in range(3):
    try: os.fstat(candidate)
    except OSError: continue
    open_standard.append(candidate)
with os.fdopen(descriptor, encoding='utf-8') as stream:
    environment = json.load(stream)
pathlib.Path(os.environ['OBSERVATION']).write_text(json.dumps({'descriptor': descriptor, 'open_standard': open_standard, 'marker': environment['CALLER_MARKER']}))
''')
        tool = self.root / "observe-transport"
        tool.write_text('#!/bin/sh\nexec ' + shlex.quote(str(TEST_PYTHON)) + ' -B -I -S ' + shlex.quote(str(observer)) + ' "$@"\n')
        tool.chmod(0o755)
        driver = '''import os, sys
sys.path[:0] = sys.argv[1:3]
from cc import runtime_test as runtime
for descriptor in sys.argv[5:]:
    os.close(int(descriptor))
raise SystemExit(runtime.prepare_native(sys.argv[3], sys.argv[4], [], [], 183))
'''
        source_root = str(Path(runtime.__file__).resolve().parents[1])
        metadata_root = str(Path(sys.modules["elf_metadata"].__file__).resolve().parent)
        for closed in ((0,), (1,), (2,), (0, 1, 2)):
            with self.subTest(closed=closed):
                output = self.root / ("closed-" + "".join(map(str, closed)) + ".json")
                child = subprocess.run(
                    [str(TEST_PYTHON), "-B", "-I", "-S", "-c", driver, source_root, metadata_root,
                     str(tool), str(self.binary), *map(str, closed)],
                    env={"PATH": "", "TMPDIR": str(self.root), "OBSERVATION": str(output),
                         "CALLER_MARKER": "closed-stdio"},
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                )
                self.assertEqual(child.returncode, 0, (closed, child.stderr))
                result = json.loads(output.read_text())
                self.assertGreaterEqual(result["descriptor"], 3)
                self.assertEqual(result["open_standard"], [fd for fd in range(3) if fd not in closed])
                self.assertEqual(result["marker"], "closed-stdio")

    def test_named_fallback_unlinks_before_environment_write(self):
        real_dump = json.dump

        def write(value, stream, **kwargs):
            self.assertEqual(os.fstat(stream.fileno()).st_nlink, 0)
            self.assertEqual(list(self.root.glob("transport-*")), [])
            return real_dump(value, stream, **kwargs)

        factory = tempfile.TemporaryFile
        with patch.object(tempfile, "_O_TMPFILE_WORKS", False), patch.object(runtime.tempfile, "TemporaryFile", side_effect=lambda **kwargs: factory(dir=self.root, prefix="transport-", **kwargs)), patch.object(runtime.json, "dump", side_effect=write), patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(runtime.prepare_native("unused", self.binary, [], [], 183), 0)

    def test_linked_transport_is_rejected_before_write_or_child(self):
        named = self.root / "should-remain-empty"
        with named.open("w+", encoding="utf-8") as stream, patch.object(runtime.tempfile, "TemporaryFile", return_value=stream), patch.object(runtime.json, "dump") as write, patch.object(runtime.subprocess, "run") as child:
            with self.assertRaisesRegex(RuntimeError, "unlinked before writing"):
                runtime.prepare_native("unused", self.binary, [], [], 183)
        write.assert_not_called()
        child.assert_not_called()
        self.assertEqual(named.read_bytes(), b"")
        self.assertTrue(stream.closed)

    def test_serialization_failure_closes_transport(self):
        before = set(os.listdir("/proc/self/fd"))
        with patch.object(runtime.json, "dump", side_effect=OSError("fixture write failure")), patch.object(runtime.subprocess, "run") as child:
            with self.assertRaisesRegex(OSError, "fixture write failure"):
                runtime.prepare_native("unused", self.binary, [], [], 183)
        child.assert_not_called()
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)

    def test_environment_descriptor_is_closed_if_child_start_fails(self):
        before = set(os.listdir("/proc/self/fd"))
        with self.assertRaises(FileNotFoundError):
            runtime.prepare_native(self.root / "absent", self.binary, [], [], 183)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)

    def test_relative_target_payload_and_argument_keep_caller_directory(self):
        tool = self.make_python_tool()
        work = self.root / "caller directory with spaces"
        work.mkdir()
        (work / "target").write_bytes(self.binary.read_bytes())
        (work / "runtime.tar").write_bytes(self.payload.read_bytes())
        (work / "relative input.txt").write_text("caller-relative-contents")
        observer = self.root / "relative-observer.py"
        observer.write_text('''import json, os, pathlib, sys
output, *command = sys.argv[1:]
pathlib.Path(output).write_text(json.dumps({'cwd': os.getcwd(), 'argument': command[-1], 'contents': pathlib.Path(command[-1]).read_text(), 'PWD': os.environ.get('PWD')}))
''')
        instrument = self.root / "relative-instrument"
        instrument.write_text('#!/bin/sh\nexec ' + shlex.quote(str(TEST_PYTHON)) + ' -I -S ' + shlex.quote(str(observer)) + ' "$@"\n')
        instrument.chmod(0o755)
        output = self.root / "relative-result.json"
        original_cwd = os.getcwd()
        try:
            os.chdir(work)
            with patch.dict(os.environ, {"PATH": "", "PWD": str(work)}, clear=True):
                result = runtime.prepare_native(tool, "target", ["runtime.tar"], ["relative input.txt"], 183,
                                                instrument, [str(output)])
            self.assertEqual(result, 0)
            self.assertEqual(os.getcwd(), str(work))
        finally:
            os.chdir(original_cwd)
        self.assertEqual(json.loads(output.read_text()), {
            "cwd": str(work), "argument": "relative input.txt", "contents": "caller-relative-contents", "PWD": str(work)})

    def test_preparation_requires_explicit_target_cpu(self):
        with self.assertRaisesRegex(ValueError, "explicit target CPU"):
            runtime.prepare_native("unused", self.binary, [], [], None)


class ConstraintSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(RUNTIME_BZL.read_text())
        function = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "_target_machine")
        self.provider = object()
        namespace = {"platform_common": SimpleNamespace(ConstraintValueInfo=self.provider),
                     "fail": lambda message: (_ for _ in ()).throw(ValueError(message))}
        # Execute only this simple pure function from the actual .bzl source.
        exec(compile(ast.Module(body=[function], type_ignores=[]), "target-constraint-selection", "exec"), namespace)
        self.select = namespace["_target_machine"]

    def context(self, cpu):
        return SimpleNamespace(attr=SimpleNamespace(_x86_64={self.provider: "x86_64"},
                                                    _aarch64={self.provider: "aarch64"}),
                               target_platform_has_constraint=lambda value: value == cpu)

    def test_target_constraints_choose_machine_independent_of_host(self):
        self.assertEqual(self.select(self.context("aarch64")), 183)
        self.assertEqual(self.select(self.context("x86_64")), 62)

    def test_unsupported_target_fails(self):
        with self.assertRaisesRegex(ValueError, "target platform"):
            self.select(self.context("riscv64"))

    def test_native_preparation_tool_has_exec_transition_and_public_api_is_retained(self):
        attrs = next(n.value for n in self.tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_attrs" for t in n.targets))
        values = {ast.literal_eval(k): v for k, v in zip(attrs.keys, attrs.values)}
        tool = values["_preparation_python"]
        self.assertEqual(next(ast.literal_eval(k.value) for k in tool.keywords if k.arg == "cfg"), "exec")
        self.assertTrue(next(ast.literal_eval(k.value) for k in tool.keywords if k.arg == "executable"))
        self.assertEqual({name for name in values if not name.startswith("_")},
                         {"binary", "runtime_payloads", "data", "instrument", "instrument_args"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
