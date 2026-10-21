#!/bin/sh
set -eu
export PATH="$BAZEL_FOREIGN_TOOLS/bin:$PATH"
"$BAZEL_FOREIGN_TOOLS/bin/autoreconf" -fi
exec /bin/sh "$BAZEL_CONFIGURE_ENV" "$BAZEL_FOREIGN_TOOLS/bin/python3" ./compiler_argv_test.py ./configure "$@"
