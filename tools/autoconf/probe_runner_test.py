"""Declared pkg-config inputs must remain isolated from ambient host paths."""
import shlex
import subprocess
import tempfile
from pathlib import Path
import unittest

from tools.autoconf.probe_runner import resolve_binutils, validate_pkg_config_paths


class PkgConfigInputsTest(unittest.TestCase):
    def test_declared_paths_in_all_gcc_forms_and_absolute_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = root / "SDK with spaces/include/api.h"
            library = root / "SDK with spaces/lib/libexample.so.3"
            header.parent.mkdir(parents=True)
            library.parent.mkdir()
            header.touch()
            library.touch()
            inputs = [str(header.relative_to(root)), str(library.relative_to(root))]
            for option in ("-I", "-isystem", "-iquote", "-idirafter"):
                for argv in ([option, str(header.parent)], [option + str(header.parent)]):
                    validate_pkg_config_paths(argv, root, inputs)
            validate_pkg_config_paths(["-L", str(library.parent), str(library), "-Wl," + str(library)], root, inputs)

    def test_existing_undeclared_host_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "ambient"
            host.mkdir()
            library = host / "libambient.a"
            library.touch()
            for argv in (["-I" + str(host)], ["-isystem", str(host)], ["-iquote" + str(host)], [str(library)], ["-Wl," + str(library)]):
                with self.assertRaisesRegex(ValueError, "not a declared accessible input"):
                    validate_pkg_config_paths(argv, root, [])

    def test_tree_artifact_is_an_explicit_input_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree = root / "declared-root"
            include = tree / "usr/include"
            include.mkdir(parents=True)
            validate_pkg_config_paths(["-I" + str(include)], root, ["declared-root"])
            with self.assertRaisesRegex(ValueError, "not a declared accessible input"):
                validate_pkg_config_paths(["-I" + str(root)], root, [])

    def test_declared_header_does_not_authorize_execroot_ancestors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = root / "api.h"
            header.touch()
            validate_pkg_config_paths(["-iquote", str(root)], root, ["api.h"])
            for path in (Path("/"), root.parent):
                with self.assertRaisesRegex(ValueError, "not a declared accessible input"):
                    validate_pkg_config_paths(["-I" + str(path)], root, ["api.h"])

    def test_missing_path_operand_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "has no argument"):
            validate_pkg_config_paths(["-isystem"], Path.cwd(), [])


class SelectedBinutilsTest(unittest.TestCase):
    def test_compiler_query_executes_declared_selected_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / "selected-nm"
            tool.write_text("#!/bin/sh\nprintf '%s\\n' 'selected target nm'\n")
            tool.chmod(0o755)
            compiler = root / "selected-cc"
            compiler.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(str(tool)) + "\n")
            compiler.chmod(0o755)
            tools, _ = resolve_binutils({"NM": "legacy-placeholder"}, [str(compiler)], root, ["selected-cc", "selected-nm"], {})
            self.assertEqual(subprocess.check_output(tools["NM"], text=True).strip(), "selected target nm")

    def test_canonical_query_path_binds_back_to_declared_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "execroot"
            root.mkdir()
            physical = parent / "repository-nm"
            physical.write_text("#!/bin/sh\nprintf '%s\\n' 'declared symlink tool'\n")
            physical.chmod(0o755)
            declared = root / "nm"
            declared.symlink_to(physical)
            compiler = root / "cc"
            compiler.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(str(physical)) + "\n")
            compiler.chmod(0o755)
            tools, _ = resolve_binutils({"NM": ""}, [str(compiler)], root, ["cc", "nm"], {})
            self.assertEqual(subprocess.check_output(tools["NM"], text=True).strip(), "declared symlink tool")

    def test_compiler_cannot_select_undeclared_ambient_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ambient = root / "ambient-nm"
            ambient.touch()
            compiler = root / "selected-cc"
            compiler.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(str(ambient)) + "\n")
            compiler.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "not a declared accessible input"):
                resolve_binutils({"NM": ""}, [str(compiler)], root, ["selected-cc"], {})


if __name__ == "__main__":
    unittest.main()
