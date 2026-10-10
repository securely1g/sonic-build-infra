"""Match source-compiled split symbols through the imported OCI rule's reader.

This exercises real toolchain ELF sections and GNU debuglink CRCs; the unit
fixtures separately cover inherited DWZ, corruption and coverage failures.
"""

from pathlib import Path
import json
import sys
import tempfile

from imported_symbols_test import layout
from sonic_oci.imported_symbols import build


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    layout(root / "image", [Path(sys.argv[1])], architecture=sys.argv[5])
    receipt = build(root / "image", [Path(sys.argv[2])], "linux/" + sys.argv[5], ["usr/bin/hello"],
                    root / "symbols.tar", root / "symbols.json")
    assert len(receipt["pairs"]) == 1, receipt
    assert receipt["excluded_paths"] == [], receipt
    assert receipt == json.loads(Path(sys.argv[4]).read_text())
    assert (root / "symbols.tar").read_bytes() == Path(sys.argv[3]).read_bytes()
