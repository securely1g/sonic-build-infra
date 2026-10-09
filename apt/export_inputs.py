#!/usr/bin/env python3
"""Export ordinary Distroless declarations from an image's reviewed APT lock."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sonic_apt.inputs import declarations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    module, _ = declarations(json.loads(args.lock.read_bytes()))
    if args.check:
        if args.module.read_text() != module:
            parser.exit(1, str(args.module) + " is stale; regenerate from the reviewed lock\n")
    else:
        args.module.write_text(module)


if __name__ == "__main__":
    main()
