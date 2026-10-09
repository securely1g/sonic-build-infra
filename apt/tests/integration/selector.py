"""Exercise the shared adapter with a minimal owner-specific base reader."""

import argparse
import json
from pathlib import Path

from sonic_apt import dependencies, selection

parser = argparse.ArgumentParser()
for arg in ("base", "lock", "retained-manifest", "mapping", "out-dir", "receipt"):
    parser.add_argument("--" + arg, required=True, type=Path)
parser.add_argument("--variant", required=True)
args = parser.parse_args()
assert (args.base / "marker").read_text() == "fixture"
architecture = json.loads(args.mapping.read_bytes())["architecture"]
# These fixture base packages satisfy the real dictionary's dependencies.
# They are metadata records only; this test does not boot or configure a base.
installed = {name: dependencies.Package(name, "1.0", architecture, origin="fixture base")
             for name in ("aspell", "dictionaries-common")}
if args.variant == "retained":
    installed["aspell-en"] = dependencies.Package("aspell-en", "2021.1", "all", origin="fixture base")
paths, receipt = selection.select(
    args.lock, args.mapping, group="dictionary", architecture=architecture,
    installed=installed,
    base_files={}, retained_packages={}, inspect_payload=lambda path: {},
    check_overlay=lambda files, base: None,
)
selection.stage_payloads(paths, args.out_dir)
args.receipt.write_text(json.dumps(receipt))
