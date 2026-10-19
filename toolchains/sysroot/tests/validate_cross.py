#!/usr/bin/env python3
"""Build ARM64 probes on AMD64, then validate their runtime on native ARM64."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile

# Resolve our sibling helper explicitly even when PYTHONSAFEPATH is enabled.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate import ARCHITECTURES, audit_graph, run_program, validate_tree
from validate_debian_compiler import artifact_paths, validate_debian_compiler, validate_public_binutils

BINARIES = ("cross_c_probe", "cross_probe", "libcross_shared_probe.so")
NOTICE = {"notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.", "generator": "toolchains/sysroot/tests/validate_cross.py"}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_elf(path, machine):
    with path.open("rb") as source:
        header = source.read(20)
    assert header[:6] == b"\x7fELF\x02\x01", path
    assert struct.unpack_from("<H", header, 18)[0] == machine, path


def dynamic(path):
    return subprocess.check_output(["readelf", "--dynamic", str(path)], text=True, env=dict(os.environ, LC_ALL="C"))


def audit_cross_graph(graph_text, compiler):
    graph = json.loads(graph_text)
    labels = {item["id"]: item["label"] for item in graph["targets"]}
    host_actions, target_actions = [], []
    for action in graph["actions"]:
        if labels[action["targetId"]].endswith("//:generate_source"):
            host_actions.append(action)
        else:
            target_actions.append(action)
    # Both audits reject DEB-producing actions and implicit host include paths.
    # The split permits AMD64 include paths only for the executable generator.
    _, compiles, target_root = audit_graph(json.dumps(dict(graph, actions=target_actions)), compiler, "arm64")
    _, _, host_root = audit_graph(json.dumps(dict(graph, actions=host_actions)), compiler, "amd64", ("generate_source.c",))
    assert target_root == host_root, (target_root, host_root)
    target_cpp = [action for action in target_actions if action["mnemonic"] in ("CppCompile", "CppLink")]
    assert all("aarch64" in " ".join(action["arguments"]) for action in target_cpp), target_cpp
    assert any(any(arg.endswith("/generated_value.c") for arg in args) for args in compiles), compiles
    generators = [action for action in target_actions if action["mnemonic"] == "Genrule"]
    assert len(generators) == 1, generators
    paths = artifact_paths(graph)
    archives = [action for action in target_actions if action["mnemonic"] == "CppArchive"]
    assert archives, "generated C code must exercise the target archiver"
    dependency_sets = {item["id"]: item for item in graph["depSetOfFiles"]}

    def inputs(identifiers):
        result = set()
        for identifier in identifiers:
            item = dependency_sets[identifier]
            result.update(item.get("directArtifactIds", []))
            result.update(inputs(item.get("transitiveDepSetIds", [])))
        return result

    generated_objects = {
        identifier for action in target_cpp
        if action["mnemonic"] == "CppCompile" and any(arg.endswith("/generated_value.c") for arg in action["arguments"])
        for identifier in action["outputIds"] if paths[identifier].endswith(".o")
    }
    assert generated_objects
    for action in archives:
        assert inputs(action["inputDepSetIds"]).intersection(generated_objects), action
    archive_paths = [paths[identifier] for action in archives for identifier in action["outputIds"]]
    host_outputs = [paths[identifier] for action in host_actions for identifier in action.get("outputIds", [])]
    host_binary = [path for path in host_outputs if path.endswith("/generate_source")]
    assert len(host_binary) == 1, host_binary
    assert host_binary[0] in " ".join(generators[0]["arguments"]), generators[0]
    return graph, compiles, target_root, host_binary[0], archive_paths


def build(args):
    assert platform.machine() == "x86_64", "Cross-build validation must run on native AMD64"
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    consumer = Path(__file__).resolve().parent / "consumer"
    receipt = {"_generated": NOTICE, "execution_architecture": "amd64", "target_architecture": "arm64", "compilers": {}}
    shared_root = None
    bundle_files = []
    for compiler, toolchains in (
        ("gcc", "@gcc_toolchains//:host_x86_64_target_aarch64_gcc_14.2.0,@gcc_toolchains//:host_x86_64_gcc_14.2.0"),
        ("clang", "@llvm_toolchain//:all"),
    ):
        evidence = artifacts / compiler
        outputs = evidence / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)

        def bazel(command, *arguments):
            target_options = [] if command == "info" else [
                "--host_platform=@sonic_build_infra//platforms:x86_64_trixie",
                "--platforms=@sonic_build_infra//platforms:aarch64_trixie",
                f"--extra_toolchains={toolchains}",
            ]
            return subprocess.check_output(
                [args.bazel, *args.startup_option, command, *args.bazel_option, *target_options, *arguments],
                cwd=consumer, text=True,
            ).strip()

        graph_text = bazel("aquery", "--output=jsonproto", "deps(set(//:cross_c_probe //:cross_probe //:readelf //:objcopy //:cross_position_independent_test))")
        (evidence / "actions.json").write_text(graph_text + "\n")
        graph, compiles, root_path, generator_path, archive_paths = audit_cross_graph(graph_text, compiler)
        # Explicitly request the library too, so optimizations that link objects
        # directly cannot leave the cross archiver untested.
        bazel("build", "//:cross_c_probe", "//:cross_probe", "//:generated_value")
        execution_root = Path(bazel("info", "execution_root"))
        root = execution_root / root_path
        declared_root = execution_root / bazel(
            "cquery", "--output=starlark",
            "--starlark:expr=target.label.workspace_root + '/' + target.label.package",
            "@sonic_sysroot//root:sysroot",
        )
        assert root.resolve() == declared_root.resolve(), (root, declared_root)
        if shared_root:
            assert shared_root.resolve() == root.resolve(), (shared_root, root)
        shared_root = root
        tree_receipt = validate_tree(root)
        archives = evidence / "archives"
        archives.mkdir(exist_ok=True)
        archive_hashes = {}
        # The graph may also contain an unused PIC archive. Inspect the static
        # archives actually requested by the cc_library's default outputs.
        library_outputs = bazel("cquery", "--output=files", "//:generated_value").splitlines()
        for relative in sorted(set(archive_paths).intersection(library_outputs)):
            source = execution_root / relative
            destination = archives / source.name
            shutil.copy2(source, destination)
            headers = subprocess.check_output(
                ["readelf", "--file-header", str(destination)], text=True, env=dict(os.environ, LC_ALL="C"),
            )
            machines = re.findall(r"Machine:\s+([^\n]+)", headers)
            assert machines and set(machines) == {"AArch64"}, headers
            (archives / f"{source.name}-elf.txt").write_text(headers)
            archive_hashes[source.name] = sha256(destination)
        assert archive_hashes, (archive_paths, library_outputs)

        def output(label):
            files = bazel("cquery", "--output=files", label).splitlines()
            assert len(files) == 1, files
            return execution_root / files[0]

        # Preserve bytes before switching toolchains: Bazel may reuse output
        # paths. The ARM64 runner must execute the exact AMD64-built artifacts.
        binary_hashes = {}
        interpreters = {}
        for name in BINARIES:
            destination = outputs / name
            shutil.copy2(output("//:" + name), destination)
            check_elf(destination, 183)
            headers = subprocess.check_output(
                ["readelf", "--file-header", "--program-headers", str(destination)],
                text=True, env=dict(os.environ, LC_ALL="C"),
            )
            (evidence / f"{name}-elf.txt").write_text(headers)
            if name != "libcross_shared_probe.so":
                interpreter = re.search(r"Requesting program interpreter: ([^]]+)\]", headers)
                assert interpreter and interpreter[1].endswith("/ld-linux-aarch64.so.1"), headers
                interpreters[name] = interpreter[1]
            binary_hashes[name] = sha256(destination)
            bundle_files.append(destination)
        library_dynamic = dynamic(outputs / "libcross_shared_probe.so")
        assert re.search(r"\(SONAME\).*\[libcross_shared_probe\.so\]", library_dynamic), library_dynamic
        (evidence / "shared-library-dynamic.txt").write_text(library_dynamic)
        generator = evidence / "generate_source"
        shutil.copy2(execution_root / generator_path, generator)
        check_elf(generator, 62)
        generated_source = output("//:generated_value_source")
        generated_text = generated_source.read_text()
        assert "AUTO-GENERATED" in generated_text and "return 42;" in generated_text, generated_text
        shutil.copy2(generated_source, evidence / "generated_value.c")
        host_triplet, _, host_loader = ARCHITECTURES["amd64"]
        host_libraries = root / f"usr/lib/{host_triplet}"
        generator_loaded = run_program(host_libraries / host_loader, host_libraries, generator, generated_text)
        if compiler == "clang":
            llvm_root = (execution_root / compiles[0][compiles[0].index("-resource-dir") + 1]).parents[2]
            compiler_path = llvm_root / "bin/clang"
        else:
            compiler_path = execution_root / compiles[0][0]
        version = subprocess.check_output([str(compiler_path), "--version"], cwd=execution_root, text=True)
        assert ("clang version 19.1.7" if compiler == "clang" else "14.2.0") in version, version
        receipt["compilers"][compiler] = {
            "compiler_version": version,
            "sysroot_label": "@sonic_sysroot//root:sysroot",
            "binary_sha256": binary_hashes,
            "archive_sha256": archive_hashes,
            "program_interpreters": interpreters,
            "generator_sha256": sha256(generator),
            "generator_elf_machine": 62,
            "target_elf_machine": 183,
            "generated_source_sha256": sha256(generated_source),
            "generator_loaded_libraries": generator_loaded,
            "action_count": len(graph["actions"]),
            "deb_outputs": [],
            **tree_receipt,
        }
        if compiler == "gcc":
            receipt["compilers"][compiler]["debian_compiler"] = validate_debian_compiler(
                execution_root, compiler_path, graph, artifact_paths(graph), evidence,
                execution_architecture="amd64", target_architecture="arm64",
            )
            receipt["compilers"][compiler]["public_binutils"] = validate_public_binutils(
                bazel, outputs / "cross_c_probe", 183, "//:cross_position_independent_test", evidence / "binutils",
            )
        shutil.copy2(consumer / "MODULE.bazel.lock", evidence / "MODULE.bazel.lock")
        print(f"{compiler}: AMD64 generator and ARM64 C/C++ outputs verified", flush=True)

    assert receipt["compilers"]["gcc"]["sysroot_tree_sha256"] == receipt["compilers"]["clang"]["sysroot_tree_sha256"]
    triplet, _, loader = ARCHITECTURES["arm64"]
    libraries = shared_root / f"usr/lib/{triplet}"
    runtime = artifacts / "runtime"
    runtime.mkdir(exist_ok=True)
    # Carry only the loader and transitive shared libraries needed by the probes,
    # not the compiler archives or the entire development sysroot.
    pending = {loader}
    for binary in bundle_files:
        pending.update(re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic(binary)))
    pending.discard("libcross_shared_probe.so")
    runtime_receipt = {}
    while pending:
        name = pending.pop()
        if name in runtime_receipt:
            continue
        assert Path(name).name == name, name
        source = libraries / name
        assert source.is_file() and source.resolve().is_relative_to(shared_root.resolve()), source
        check_elf(source, 183)
        destination = runtime / name
        shutil.copy2(source.resolve(), destination)
        runtime_receipt[name] = {"sha256": sha256(destination), "sysroot_source": f"usr/lib/{triplet}/{name}"}
        bundle_files.append(destination)
        pending.update(re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic(source)))
    receipt["runtime"] = runtime_receipt
    receipt["runtime_validation"] = "pending native ARM64 job"
    manifest = artifacts / "build-validation.json"
    manifest.write_text(json.dumps(receipt, indent=2) + "\n")
    with tarfile.open(artifacts / "runtime-bundle.tar.gz", "w:gz") as archive:
        for path in [manifest, *sorted(bundle_files)]:
            archive.add(path, arcname=path.relative_to(artifacts).as_posix(), recursive=False)
    print("ARM64 runtime bundle ready for native execution")


def run(args):
    assert platform.machine() == "aarch64", "Cross-built programs must be executed on native ARM64"
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    unpacked = artifacts / "bundle"
    with tarfile.open(args.bundle) as archive:
        archive.extractall(unpacked, filter="data")
    build_receipt = json.loads((unpacked / "build-validation.json").read_text())
    assert build_receipt["execution_architecture"] == "amd64"
    assert build_receipt["target_architecture"] == "arm64"
    runtime = unpacked / "runtime"
    for name, expected in build_receipt["runtime"].items():
        assert sha256(runtime / name) == expected["sha256"], name
        check_elf(runtime / name, 183)
    binaries = {}
    for compiler in ("gcc", "clang"):
        binaries[compiler] = unpacked / compiler / "outputs"
        for name, digest in build_receipt["compilers"][compiler]["binary_sha256"].items():
            assert sha256(binaries[compiler] / name) == digest, (compiler, name)
            check_elf(binaries[compiler] / name, 183)
    loader = runtime / ARCHITECTURES["arm64"][2]
    loaded = {}
    for compiler in ("gcc", "clang"):
        outputs = binaries[compiler]
        loaded[f"{compiler}_c"] = run_program(loader, runtime, outputs / "cross_c_probe", "SONiC C sysroot 8\n")
        loaded[f"{compiler}_cpp"] = run_program(
            loader, runtime, outputs / "cross_probe", "SONiC shared sysroot 42 8\n", outputs / "libcross_shared_probe.so",
        )
    for caller, library in (("gcc", "clang"), ("clang", "gcc")):
        loaded[f"{caller}_caller_{library}_library"] = run_program(
            loader, runtime, binaries[caller] / "cross_probe", "SONiC shared sysroot 42 8\n",
            binaries[library] / "libcross_shared_probe.so",
        )
    receipt = {
        "_generated": NOTICE,
        "execution_architecture": "arm64",
        "build_execution_architecture": "amd64",
        "runtime_validation": "passed on native ARM64",
        "bundle_sha256": sha256(args.bundle),
        "loaded_libraries": loaded,
        "build_validation": build_receipt,
    }
    (artifacts / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("Native ARM64 passed C, C++, threads, exceptions and swapped GCC/Clang shared libraries")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="Cross-compile on native AMD64")
    build_parser.add_argument("--bazel", default="bazel")
    build_parser.add_argument("--startup-option", action="append", default=[])
    build_parser.add_argument("--bazel-option", action="append", default=[])
    build_parser.add_argument("--artifacts", required=True, type=Path)
    run_parser = commands.add_parser("run", help="Execute cross-built artifacts on native ARM64")
    run_parser.add_argument("--bundle", required=True, type=Path)
    run_parser.add_argument("--artifacts", required=True, type=Path)
    args = parser.parse_args()
    (build if args.command == "build" else run)(args)


if __name__ == "__main__":
    main()
