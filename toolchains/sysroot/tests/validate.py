#!/usr/bin/env python3
"""Compile with the public LLVM API and run against the declared SONiC sysroot."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--startup-option", action="append", default=[])
    parser.add_argument("--bazel-option", action="append", default=[])
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    consumer = Path(__file__).resolve().parent / "consumer"
    architecture, triplet, loader_name = {
        "x86_64": ("amd64", "x86_64-linux-gnu", "ld-linux-x86-64.so.2"),
        "aarch64": ("arm64", "aarch64-linux-gnu", "ld-linux-aarch64.so.1"),
    }[platform.machine()]

    def bazel(command, *arguments):
        return subprocess.check_output(
            [args.bazel, *args.startup_option, command, *args.bazel_option, *arguments],
            cwd=consumer, text=True,
        ).strip()

    # Analysis must precede any build. This independent consumer has no package
    # producer and uses the same public repository API as the SymCrypt build.
    graph_text = bazel("aquery", "--output=jsonproto", "deps(//:probe)")
    (artifacts / "actions.json").write_text(graph_text + "\n")
    graph = json.loads(graph_text)
    fragments = {item["id"]: item for item in graph["pathFragments"]}

    def path(identifier):
        item = fragments[identifier]
        prefix = path(item["parentId"]) + "/" if item.get("parentId") else ""
        return prefix + item["label"]

    outputs = {item["id"]: path(item["pathFragmentId"]) for item in graph["artifacts"]}
    actions = graph["actions"]
    assert actions, "empty action graph"
    for action in actions:
        assert all(not outputs[item].endswith(".deb") for item in action.get("outputIds", []))
        command = " ".join(action.get("arguments", []))
        assert not re.search(r"(?:^|[/\s])(?:make|gmake|dpkg-buildpackage|dpkg-deb)(?:\s|$)", command), command
    compile_args = next(item["arguments"] for item in actions if item["mnemonic"] == "CppCompile")
    link_args = next(item["arguments"] for item in actions if item["mnemonic"] == "CppLink")
    assert "-nostdinc" in compile_args and "-nostdinc++" in compile_args
    assert "-fuse-ld=lld" in link_args
    sysroot_arg = next(item for item in compile_args if item.startswith("--sysroot="))
    assert sysroot_arg in link_args
    assert not any(item.startswith(("-I/usr", "-L/usr", "--sysroot=/")) for item in compile_args + link_args)
    for index, item in enumerate(compile_args):
        if item in ("-idirafter", "-cxx-isystem"):
            assert compile_args[index + 1].startswith("external/"), compile_args[index + 1]

    bazel("build", "//:probe")
    execution_root = Path(bazel("info", "execution_root"))
    root = execution_root / sysroot_arg.split("=", 1)[1]
    assert os.readlink(root / "lib") == "usr/lib"
    assert (root / "usr/include/linux/types.h").is_file()
    assert (root / "usr/include/c++/14/vector").is_file()
    assert not any(p.is_symlink() and os.readlink(p).startswith("/") for p in root.rglob("*"))
    assert not any(p.is_symlink() and not p.exists() for p in root.rglob("*")), "incomplete development-library links"

    # Prove the merged linker script bytes still come directly from the pinned
    # repository archive; the bridge must not rewrite Debian's linker paths.
    inputs = json.loads((root.parent / "inputs.json").read_text())
    assert inputs["architecture"] == architecture and len(inputs["archives"]) == {"amd64": 17, "arm64": 16}[architecture]
    archive_hashes = {}
    linker_script = root / f"usr/lib/{triplet}/libc.so"
    for label in inputs["archives"]:
        # Repository inputs need no action, so Bazel need not link them into
        # the execution root. Read the source repository next to the sysroot.
        repository = label.split("//", 1)[0].removeprefix("@@")
        source = root.resolve().parent.parent / repository / "data.tar.xz"
        assert source.is_file() and source.name == "data.tar.xz"
        archive_hashes[label] = hashlib.sha256(source.read_bytes()).hexdigest()
        if "_libc6-dev-" in label:
            with tarfile.open(source) as archive:
                member = archive.extractfile(f"./usr/lib/{triplet}/libc.so")
                assert member is not None and member.read() == linker_script.read_bytes()

    binary = execution_root / bazel("cquery", "--output=files", "//:probe")
    libraries = root / f"usr/lib/{triplet}"
    loader = libraries / loader_name
    invocation = [str(loader), "--library-path", str(libraries), str(binary)]
    assert subprocess.check_output(invocation, text=True) == "SONiC Clang sysroot 8\n"
    loaded = subprocess.check_output([str(loader), "--list", *invocation[1:]], text=True)
    for line in loaded.splitlines():
        if "=>" in line:
            assert str(root) in line, line
    llvm_root = (execution_root / compile_args[compile_args.index("-resource-dir") + 1]).parents[2]
    clang_version = subprocess.check_output([str(llvm_root / "bin/clang"), "--version"], text=True)
    assert "clang version 19.1.7" in clang_version, clang_version
    receipt = {
        "_generated": {"notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.", "generator": "toolchains/sysroot/tests/validate.py"},
        "architecture": architecture, "action_count": len(actions), "deb_outputs": [],
        "clang_version": clang_version, "compile_arguments": compile_args,
        "link_arguments": link_args, "loaded_libraries": loaded,
        "source_archive_sha256": archive_hashes,
        "linker_script_sha256": hashlib.sha256(linker_script.read_bytes()).hexdigest(),
    }
    (artifacts / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Validated Clang 19.1.7 with the native {architecture} SONiC sysroot")


if __name__ == "__main__":
    main()
