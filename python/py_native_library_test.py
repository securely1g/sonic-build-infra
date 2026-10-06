"""Exercise the generated wrapper's process behavior after a native preload."""

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

NATIVE_EXTENSION = None

def wrapper_template():
    # Read the rule's real template without duplicating the generated loader.
    source = Path(__file__).with_name("py_native_library.bzl").read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_INIT_PY_TEMPLATE"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("native wrapper template is missing")


class NativeLibraryProcessTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "probe"
        self.package.mkdir()
        # The compiled test extension uses the stable Python C API. No fake
        # ctypes loader or fake extension module replaces its native behavior.
        self.extension = NATIVE_EXTENSION
        (self.root / "native.so").symlink_to(self.extension)
        (self.package / "_process_probe.so").symlink_to(self.extension)
        (self.package / "process_probe.py").write_text("from ._process_probe import *\n")
        (self.package / "__init__.py").write_text(wrapper_template().format(
            package_name="probe", module_name="process_probe",
            lib_paths={"native": ["native.so"]},
        ))

    def run_process(self, body, *, fallback=True):
        prelude = ""
        if fallback:
            # Force only the initial relative import to fail, as it does when a
            # runtime library is not on the system path. The fallback then
            # preloads a real ELF and imports the compiled extension itself.
            prelude = textwrap.dedent("""\
                import sys
                class InitialImportFailure:
                    def find_spec(self, fullname, path=None, target=None):
                        if fullname == 'probe._process_probe':
                            sys.meta_path.remove(self)
                            raise ImportError('exercise native preload fallback')
                sys.meta_path.insert(0, InitialImportFailure())
                """)
        env = os.environ.copy()
        env.pop("PYTHONUNBUFFERED", None)
        env.update(PYTHONPATH=str(self.root), RUNFILES_DIR=str(self.root))
        return subprocess.run(
            [sys.executable, "-B", "-c", prelude + textwrap.dedent(body)],
            env=env, text=True, capture_output=True, check=False,
        )

    def test_success_preserves_buffered_stdout(self):
        result = self.run_process("""\
            import probe
            print('generated', probe.value())
            """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "generated 42\n")

    def test_explicit_exit_status_is_preserved(self):
        result = self.run_process("import probe; raise SystemExit(23)")
        self.assertEqual(result.returncode, 23, result.stderr)

    def test_unhandled_exception_fails(self):
        result = self.run_process("import probe; raise ValueError('invalid template')")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ValueError: invalid template", result.stderr)

    def test_failed_extension_import_fails(self):
        (self.package / "_process_probe.so").unlink()
        result = self.run_process("import probe", fallback=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ImportError", result.stderr)

    def test_existing_shutdown_handlers_run(self):
        result = self.run_process("""\
            import atexit
            atexit.register(print, 'shutdown completed')
            import probe
            """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "shutdown completed\n")

    def test_direct_import_still_works(self):
        result = self.run_process("import probe; print(probe.value())", fallback=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "42\n")

    def test_generated_rule_excludes_sanitizers_from_preload(self):
        # This package comes from the actual py_native_library rule, including
        # CcInfo collection and library filtering. Its hwasan-named dependency
        # exits 99 from its ELF constructor if it is mistakenly preloaded.
        body = textwrap.dedent("""\
            import sys
            class InitialImportFailure:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'process_probe._process_probe':
                        sys.meta_path.remove(self)
                        raise ImportError('exercise native preload fallback')
            sys.meta_path.insert(0, InitialImportFailure())
            import process_probe
            print(process_probe.value())
            """)
        result = subprocess.run(
            [sys.executable, "-B", "-c", body],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "42\n")


if __name__ == "__main__":
    NATIVE_EXTENSION = Path(sys.argv.pop(1)).absolute()
    unittest.main()
