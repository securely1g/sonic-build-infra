"""Fixed GDB must read source lines and its own embedded Python runtime."""
import ast
import os
from pathlib import Path
import subprocess
import sys
import unittest

program = str(Path(sys.argv[1]).absolute())
fixture, runtime = [str(Path(value).resolve()) for value in sys.argv[2:]]
sys.argv[1:] = []


class GdbRuntimeTest(unittest.TestCase):
    def test_source_line_lookup_and_embedded_python_ignore_host_paths(self):
        environment = dict(os.environ, PATH='', PYTHONHOME='/absent/host-python',
                           PYTHONPATH='/absent/host-python', PYTHONUSERBASE='/absent/host-user',
                           LD_LIBRARY_PATH='/absent/host-libraries')
        result = subprocess.run([program, '-nx', '-batch', '-ex', 'set debuginfod enabled off',
                                 '-ex', 'file ' + fixture, '-ex', 'info line declared_gdb_contract',
                                 '-ex', "python import sys; print('DECLARED_GDB_PYTHON=%d.%d' % sys.version_info[:2])",
                                 '-ex', "python import pathlib; print('DECLARED_GDB_DSOS='+repr(sorted({line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines() if '/' in line.split()[-1] and '.so' in line.split()[-1]})))"],
                                env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('gdb_fixture.c', result.stdout)
        self.assertIn('Line 1', result.stdout)
        self.assertIn('DECLARED_GDB_PYTHON=3.13', result.stdout)
        loaded = ast.literal_eval(next(line.split('=', 1)[1] for line in result.stdout.splitlines()
                                       if line.startswith('DECLARED_GDB_DSOS=')))
        self.assertTrue(loaded)
        declared_files = {path.resolve() for path in Path(runtime).rglob("*") if path.is_file()}
        self.assertTrue(all(Path(item).resolve() in declared_files for item in loaded), (runtime, loaded))


if __name__ == '__main__':
    unittest.main()
