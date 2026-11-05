"""Exercise declared Tcl packages and Expect subprocesses without host Tcl."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TCLSH = str(Path(sys.argv.pop(1)).absolute())


class TclRuntimeTest(unittest.TestCase):
    def test_standard_library_and_expect_process(self):
        with tempfile.TemporaryDirectory(prefix="Tcl consumer ") as directory:
            child = Path(directory) / "child.tcl"
            child.write_text('puts "declared child: [expr {6 * 7}]"\n')
            program = Path(directory) / "consumer.tcl"
            program.write_text('''
package require tcltest
package require Expect
set timeout 5
spawn -noecho [lindex $argv 0] [lindex $argv 1]
expect {
    "declared child: 42" {}
    timeout {error "declared Tcl child produced no expected output"}
    eof {error "declared Tcl child exited before expected output"}
}
expect eof
set status [wait]
if {[lindex $status 3] != 0} {error "child failed: $status"}
puts "Tcl packages and Expect process passed"
''')
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(("TCL", "LD_"))}
            environment.update(PATH="", TCL_LIBRARY="/missing-host-tcl", TCLLIBPATH="/missing-host-packages")
            for interpreter in (TCLSH,):
                with self.subTest(interpreter=interpreter):
                    result = subprocess.run([interpreter, str(program), TCLSH, str(child)],
                                            cwd=directory, env=environment, text=True,
                                            capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("Tcl packages and Expect process passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
