#!/usr/bin/env python3
"""Identify the image recipe and record the packages actually installed."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

RECIPE_FILES = ("Dockerfile", "packages.txt", "identity.py", "smoke_test.py")


def recipe_sha256(directory):
    digest = hashlib.sha256()
    for name in RECIPE_FILES:
        digest.update(name.encode() + b"\0" + (directory / name).read_bytes() + b"\0")
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-marker", metavar="EXPECTED_RECIPE_SHA256")
    args = parser.parse_args()
    recipe = recipe_sha256(Path(__file__).resolve().parent)
    if args.write_marker is None:
        print(recipe)
        return
    if args.write_marker != recipe:
        raise SystemExit("image recipe differs from requested recipe identity")
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if architecture not in ("amd64", "arm64"):
        raise SystemExit("unsupported execution architecture: " + architecture)
    packages = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Status}\n"], text=True,
    )
    marker = {
        "schema_version": 1,
        "recipe_sha256": recipe,
        "architecture": architecture,
        "debian_snapshot": "20260727T143429Z",
        "security_snapshot": "20260726T121236Z",
        "bazel_version": "8.5.1",
        "packages": {
            name: version for name, version, status in
            (line.split("\t") for line in packages.splitlines()) if status == "installed"
        },
    }
    Path("/etc/sonic-build-tools.json").write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
