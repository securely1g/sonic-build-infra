# Source before the first exec: . configure_linker_flags.sh PYTHON COMMAND [ARG...]
# PYTHON is a declared execution-side interpreter. configure_environment.sh must
# create its compiler launcher in this same directory to consume the vector.
(
    set -eu
    sonic_python=${1:?a declared Python interpreter is required}
    shift
    : "${1:?a configure command is required}"
    sonic_linker_arguments="$PWD/.sonic-linker-arguments"
    # Only shell builtins may run until the oversized environment entry is gone.
    printf '%s\n' "${LDFLAGS:-}" > "$sonic_linker_arguments.raw"
    export LDFLAGS="@$sonic_linker_arguments"
    "$sonic_python" - "$sonic_linker_arguments.raw" "$sonic_linker_arguments" <<'PY'
import pathlib
import shlex
import sys

arguments = shlex.split(pathlib.Path(sys.argv[1]).read_text())
if any("\n" in value or "\r" in value for value in arguments):
    raise ValueError("Linker arguments cannot contain line breaks")
pathlib.Path(sys.argv[2]).write_text(
    "sonic-linker-arguments-v1\n" + "".join(value + "\n" for value in arguments)
)
PY
    "$@"
)
