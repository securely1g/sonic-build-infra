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
assert len(argv["long_library_commands"]) == 2
assert all(case["original_failed_e2big"] and case["adapted_linked_and_ran"] for case in argv["long_library_commands"])
assert len(argv["library_cases"]) == 8
assert argv["library_path_arguments_preserved"]
assert len(argv["system_include_cases"]) == 12
assert len(argv["header_warning_cases"]) == 4
assert all(case["success"] == (case["kind"] == "-isystem") for case in argv["header_warning_cases"])
assert len(argv["long_system_commands"]) == 2
assert all(case["original_failed_e2big"] and case["adapted_compiled_and_ran"] for case in argv["long_system_commands"])
assert argv["system_operands_and_response_files_preserved"]
assert argv["autoconf_native_header_arguments"] > 100
assert "configure-" in log and "-iquote" in log and "-isystem" in log
