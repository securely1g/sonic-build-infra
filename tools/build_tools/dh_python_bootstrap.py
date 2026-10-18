"""Point dh-python's fixed lookup locations at its declared execution runtime."""
import os
from pathlib import Path
import runpy
import sys
import subprocess

root = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(root / 'usr/share/dh-python'))
import dhpython
from dhpython.interpreter import Interpreter

# Distribution mappings and package searches must use declared inputs. Keep
# dh-python's normal metadata scanning; redirect its fixed dpkg executable so
# the fallback uses our private DPKG_ADMINDIR, never the host's package database.
dhpython.PYDIST_DIRS['cpython3'] = str(root / 'usr/share/python3/dist')
os.environ['DH_PYTHON_DIST'] = str(root / 'usr/share/dh-python/dist')
subprocess_run = subprocess.run

def declared_dpkg(args, *positional, **keywords):
    if isinstance(args, (list, tuple)) and args and args[0] == '/usr/bin/dpkg':
        args = [str(root / 'bin/dpkg'), *args[1:]]
    return subprocess_run(args, *positional, **keywords)

subprocess.run = declared_dpkg

# dh-python has environment settings for Python versions, but its ABI query
# still names /usr/bin/python3.X. Change only that query's executable directory;
# installed shebangs and package paths retain their original Debian values.
execute = Interpreter._execute

def declared_interpreter(self, command, version=None, cache=True):
    path = self.path
    try:
        self.path = str(root / 'bin') + '/'
        return execute(self, command, version, cache)
    finally:
        self.path = path

Interpreter._execute = declared_interpreter
sys.argv[0] = str(root / 'usr/bin/dh_python3')
runpy.run_path(sys.argv[0], run_name='__main__')
