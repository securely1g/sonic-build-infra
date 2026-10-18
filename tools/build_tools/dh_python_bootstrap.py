"""Point dh-python's fixed lookup locations at its declared execution runtime."""
import os
from pathlib import Path
import runpy
import sys

root = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(root / 'usr/share/dh-python'))
import dhpython
from dhpython.interpreter import Interpreter

# Distribution name/version mappings must come from declared files. Disable
# the optional fallback that searches packages installed on the build host.
dhpython.PYDIST_DIRS['cpython3'] = str(root / 'usr/share/python3/dist')
os.environ['DH_PYTHON_DIST'] = str(root / 'usr/share/dh-python/dist')

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
sys.argv.insert(1, '--no-guessing-deps')
runpy.run_path(sys.argv[0], run_name='__main__')
