"""Check interpreter routing and relocation for private Debian helper scripts."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from kernel_debian import _private_scripts


class PrivateScriptsTest(unittest.TestCase):
    def test_private_script_uses_declared_interpreter_after_runtime_moves(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "original tools"
            (root / "bin").mkdir(parents=True)
            interpreter = root / "bin/perl"
            interpreter.write_text('#!/bin/sh\nprintf "declared:%s:%s:%s\\n" "$1" "$2" "$3"\n')
            interpreter.chmod(0o755)
            directory = root / "usr/share/kernel-wedge/commands"
            directory.mkdir(parents=True)
            script = directory / "helper with space"
            body = "#!/usr/bin/perl -w\n# Original helper body.\n"
            script.write_text(body)
            script.chmod(0o755)
            template = directory / "maintainer-template"
            template.write_text("#!/bin/sh\necho /usr/lib/installed-package\n")
            template.chmod(0o644)
            _private_scripts(root, "usr/share/kernel-wedge")
            moved = Path(temporary) / "relocated tools"
            shutil.move(root, moved)
            executable = moved / script.relative_to(root)
            result = subprocess.run([str(executable), "argument with space"],
                                    env={"PATH": "", "SONIC_BUILD_TOOLS_ROOT": str(moved)},
                                    text=True, capture_output=True, check=True)
            saved = moved / "share/sonic-build-tools/debian-scripts" / script.relative_to(root)
            self.assertEqual(result.stdout, f"declared:-w:{saved}:argument with space\n")
            self.assertEqual(saved.read_text(), body)
            self.assertEqual((moved / template.relative_to(root)).read_text(),
                             "#!/bin/sh\necho /usr/lib/installed-package\n")
            self.assertNotIn(str(root), executable.read_text())

    def test_unknown_helper_interpreter_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "helpers/unknown"
            script.parent.mkdir()
            script.write_text("#!/usr/bin/undeclared-language\n")
            script.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "unsupported declared helper interpreter"):
                _private_scripts(root, "helpers")


if __name__ == "__main__":
    unittest.main()
