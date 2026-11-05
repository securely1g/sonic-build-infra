"""An embedded Python host tool uses its declared ABI/stdlib after relocation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

executable, runtime = [Path(x).absolute() for x in sys.argv[1:]]
with tempfile.TemporaryDirectory(dir=os.environ['TEST_TMPDIR']) as temporary:
    directory = Path(temporary)
    module = directory / 'modules'
    module.mkdir()
    (module / 'caller_input.py').write_text('value = 42\n')
    environment = dict(os.environ, PATH='', LD_LIBRARY_PATH=str(directory),
                       LD_PRELOAD='', LD_AUDIT='', PYTHONHOME='/missing-python-home',
                       PYTHONPATH=str(module))

    def check(program, expected_runtime):
        result = subprocess.run([str(program)], cwd=directory, env=environment,
                                text=True, capture_output=True, check=True)
        value = json.loads(result.stdout)
        assert value['version'] == [3, 13], value
        assert value['value'] == 42, value
        assert Path(value['prefix']).resolve() == (expected_runtime / 'usr').resolve(), value
        # Bazel may represent a declared tree input as individual file symlinks.
        # Verify Python's lookup namespace; the relocated copy below is materialized.
        assert Path(value['stdlib']).absolute().is_relative_to(Path(value['prefix']).absolute()), value

    check(executable, runtime)
    roots = [p for p in executable.parents if p.name.endswith('.runfiles')]
    assert roots, executable
    original = roots[0]
    relocated = directory / 'relocated.runfiles'
    shutil.copytree(original, relocated, symlinks=False)
    check(relocated / executable.relative_to(original),
          relocated / runtime.relative_to(original))
