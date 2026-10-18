#!/usr/bin/env python3
"""Require native AMD64 and ARM64 validation to use the same combined sysroot."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path, nargs=2)
    args = parser.parse_args()
    receipts = [json.loads(path.read_text()) for path in args.receipts]
    assert {receipt["architecture"] for receipt in receipts} == {"amd64", "arm64"}
    reference = receipts[0]["compilers"]["gcc"]
    assert len(reference["source_archive_sha256"]) == 33
    for receipt in receipts:
        assert receipt["sysroot_label"] == "@sonic_sysroot//root:sysroot"
        assert receipt["sysroot_architectures"] == ["amd64", "arm64"]
        assert set(receipt["compilers"]) == {"gcc", "clang"}
        for compiler, validation in receipt["compilers"].items():
            for field in (
                "sysroot_architectures", "source_archive_sha256",
                "sysroot_tree_sha256", "elf_machines", "linker_script_sha256",
                "collision_check",
            ):
                assert validation[field] == reference[field], (receipt["architecture"], compiler, field)
    print("Native AMD64 and ARM64 builds used the same 33 archives and combined sysroot tree")
    print("Sysroot SHA256:", reference["sysroot_tree_sha256"])


if __name__ == "__main__":
    main()
