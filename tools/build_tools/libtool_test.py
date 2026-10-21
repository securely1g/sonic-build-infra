"""Run a shared-library program built through declared Autoconf/Libtool tools."""
import os
import json
from pathlib import Path
import subprocess
import sys

binary, library, c_library, config, argv_validation = [Path(p).resolve() for p in sys.argv[1:]]
assert c_library.parent == library.parent
env = dict(os.environ, LD_LIBRARY_PATH=str(library.parent))
assert subprocess.check_output([binary], env=env, text=True) == "42\n"
log = config.read_text()
assert "lt_cv_prog_gnu_ld=yes" in log
assert "CXXCPP='/lib/cpp'" not in log
assert "CPP='/lib/cpp'" not in log
assert "--sysroot=" in log
argv = json.loads(argv_validation.read_text())
assert len(argv["include_cases"]) == 8
assert len(argv["long_commands"]) == 2
assert all(case["original_failed_e2big"] and case["adapted_compiled_and_ran"] for case in argv["long_commands"])
assert argv["linker_and_other_operands_preserved"]
