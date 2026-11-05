"""Declared inputs keep their meaning across configure cwd changes; loader paths do not move."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from probe_runner import launcher, relative_arguments


class ProbeArgvPathsTest(unittest.TestCase):
    def test_input_paths_resolve_from_each_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            header = root / "inputs" / "marker.h"
            header.parent.mkdir()
            header.write_text("declared marker\n")
            for directory in [root / "configuration", root / "configuration" / "nested"]:
                directory.mkdir(parents=True, exist_ok=True)
                result = relative_arguments(["-I", str(header.parent), "-I" + str(header.parent), "--sysroot=" + str(root), str(header)], root, directory)
                self.assertFalse(os.path.isabs(result[1]))
                self.assertEqual((directory / result[1] / "marker.h").read_text(), "declared marker\n")
                self.assertEqual((directory / result[2][2:] / "marker.h").read_text(), "declared marker\n")
                self.assertEqual((directory / result[3].split("=", 1)[1]).resolve(), root)
                self.assertEqual((directory / result[4]).read_text(), "declared marker\n")

    def test_linker_state_and_runtime_paths_keep_their_meaning(self):
        root, cwd = "/declared/execroot", "/declared/execroot/configuration/nested"
        library = root + "/lib/libsample.a"
        rpath = root + "/runtime"
        args = ["-Wl,--whole-archive", library, "-Wl,--no-whole-archive", "-Wl,--version-script=" + root + "/exports.map", "-Wl,-rpath," + rpath, "-Wl,-rpath-link," + root + "/lib", "-Xlinker", "-rpath", "-Xlinker", rpath, "-Wl,--dynamic-linker=" + root + "/ld.so", "-I/outside/include", "-DNAME=" + root + "/literal"]
        result = relative_arguments(args, root, cwd)
        self.assertEqual(len(result), len(args))
        self.assertEqual(Path(cwd, result[1]).resolve(), Path(library))
        self.assertFalse(os.path.isabs(result[1]))
        self.assertEqual(Path(cwd, result[3].split("=", 1)[1]).resolve(), Path(root + "/exports.map"))
        self.assertEqual(Path(cwd, result[5].split(",", 2)[2]).resolve(), Path(root + "/lib"))
        for index in [0, 2, 4, 6, 7, 8, 9, 10, 11, 12]:
            self.assertEqual(result[index], args[index])

    def test_launcher_uses_each_invocations_cwd_and_keeps_caller_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.c"
            source.write_text("declared source\n")
            recorder = root / "record.py"
            recorder.write_text("import json,os,pathlib,sys\nprint(json.dumps({'cwd':os.getcwd(),'source':pathlib.Path(sys.argv[1]).read_text(),'argument':sys.argv[1],'forwarded':sys.argv[2:],'python':sys.executable}))\n")
            wrapper = root / "CC"
            launcher(wrapper, [sys.executable, str(recorder), str(source)], Path(sys.executable), root=root)
            for directory in [root / "work", root / "work" / "nested"]:
                directory.mkdir(parents=True, exist_ok=True)
                result = json.loads(subprocess.check_output([str(wrapper), "-c", "caller-relative.c"], cwd=directory, text=True))
                self.assertEqual(result["cwd"], str(directory))
                self.assertEqual(result["source"], "declared source\n")
                self.assertFalse(os.path.isabs(result["argument"]))
                self.assertEqual(result["forwarded"], ["-c", "caller-relative.c"])
                self.assertEqual(Path(result["python"]).resolve(), Path(sys.executable).resolve())


if __name__ == "__main__":
    unittest.main()
