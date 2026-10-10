"""Wrap an existing runtime TAR in a small OCI envelope for integration tests."""

from pathlib import Path
import sys

from imported_symbols_test import layout

layout(Path(sys.argv[2]), [Path(sys.argv[1])], architecture=sys.argv[3])
