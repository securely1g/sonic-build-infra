#!/usr/bin/env python3
"""Compile with GCC and Clang, then run both against one declared SONiC sysroot."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile

# Resolve our sibling helper even when PYTHONSAFEPATH is enabled.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_debian_compiler import artifact_paths, validate_debian_compiler, validate_public_binutils

ARCHITECTURES = {
    "amd64": ("x86_64-linux-gnu", 62, "ld-linux-x86-64.so.2"),
    "arm64": ("aarch64-linux-gnu", 183, "ld-linux-aarch64.so.1"),
}


def audit_graph(graph_text, compiler, architecture, required_sources=("c_probe.c", "probe.cc", "shared_probe.cc")):
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
        assert "--remap-inputs" not in command, command
    native = [item for item in actions if item["mnemonic"] in ("CppCompile", "CppLink")]
    compiles = [item["arguments"] for item in native if item["mnemonic"] == "CppCompile"]
    assert all(any(source in args for args in compiles) for source in required_sources), "missing C or C++ compilation"
    sysroots = set()
    for action in native:
        arguments = action["arguments"]
        roots = [item.split("=", 1)[1] for item in arguments if item.startswith("--sysroot=")]
        assert len(roots) == 1, arguments
        sysroots.update(roots)
        assert not roots[0].startswith("/"), roots[0]
        for index, item in enumerate(arguments):
            assert not item.startswith(("-I/usr", "-L/usr", "-I/lib", "-L/lib")), arguments
            if item in ("-isystem", "-idirafter", "-cxx-isystem", "-I", "-L", "-B"):
                assert not arguments[index + 1].startswith(("/usr", "/lib")), arguments
            assert not re.search(r"trixie(?:-security)?_(?:libc6|linux-libc-dev|libgcc|libstdc)", item), item
            for other, (triplet, _, _) in ARCHITECTURES.items():
                if other != architecture:
                    assert f"/{triplet}/" not in item, (architecture, item)
        if action["mnemonic"] == "CppCompile":
            if compiler == "clang":
                assert "-resource-dir" in arguments, arguments
            else:
                assert "-nostdinc" in arguments, arguments
                if any(source.endswith(".cc") for source in arguments):
                    assert "-nostdinc++" in arguments, arguments
                assert re.search(r"/bin/[^/]*-(?:gcc|g\+\+)$", arguments[0]), arguments
        elif compiler == "clang":
            assert "-fuse-ld=lld" in arguments, arguments
    assert len(sysroots) == 1, sysroots
    return actions, compiles, sysroots.pop()


def validate_tree(root):
    assert os.readlink(root / "lib") == "usr/lib"
    assert (root / "usr/include/linux/types.h").is_file()
    assert (root / "usr/include/c++/14/vector").is_file()
    # Compare contents rather than timestamps: the same combined tree must be
    # assembled on either host architecture, including shared header choices.
    tree_hash = hashlib.sha256()
    for entry in sorted(root.rglob("*")):
        relative = entry.relative_to(root).as_posix()
        mode = stat.S_IMODE(entry.lstat().st_mode)
        if entry.is_symlink():
            target = os.readlink(entry)
            assert not target.startswith("/"), (relative, target)
            assert entry.exists(), f"incomplete development-library link: {relative}"
            assert entry.resolve().is_relative_to(root.resolve()), (relative, target)
            value = [relative, "symlink", mode, target]
        elif entry.is_file():
            value = [relative, "file", mode, hashlib.sha256(entry.read_bytes()).hexdigest()]
        else:
            continue
        tree_hash.update(json.dumps(value, separators=(",", ":")).encode() + b"\n")

    # Inspect both targets, regardless of which compiler can run on this host.
    # The ELF e_machine field catches a library from the wrong architecture.
    elf_machines = {}
    scripts = set()
    for architecture, (triplet, machine, loader) in ARCHITECTURES.items():
        for header in (
            f"usr/include/{triplet}/bits/wordsize.h",
            f"usr/include/{triplet}/asm/unistd.h",
            f"usr/include/{triplet}/c++/14/bits/c++config.h",
        ):
            assert (root / header).is_file(), header
        for name in ("libc.so.6", "libm.so.6", "libgcc_s.so.1", "libstdc++.so.6", loader):
            relative = f"usr/lib/{triplet}/{name}"
            with (root / relative).open("rb") as library:
                elf = library.read(20)
            assert elf[:6] == b"\x7fELF\x02\x01", relative
            actual_machine = struct.unpack_from("<H", elf, 18)[0]
            assert actual_machine == machine, (relative, actual_machine, architecture)
            elf_machines[relative] = actual_machine
        scripts.update(f"usr/lib/{triplet}/{name}" for name in ("libc.so", "libm.so", "libm.a"))

    inputs = json.loads((root.parent / "inputs.json").read_text())
    assert inputs["architectures"] == ["amd64", "arm64"], inputs["architectures"]
    assert len(inputs["archives"]) == 33
    assert len(set(inputs["archives"])) == 33, "duplicate source archive"
    collision_check = inputs["collision_check"]
    assert collision_check["comparison"] == "exact bytes", collision_check
    assert collision_check["shared_files"] > 0, collision_check
    assert collision_check["shared_symlinks"] > 0, collision_check
    assert collision_check["shared_directories"] > 0, collision_check
    assert collision_check["shared_paths"] == sum(collision_check[key] for key in (
        "shared_files", "shared_symlinks", "shared_directories",
    )), collision_check
    assert collision_check["paths"] >= collision_check["shared_paths"], collision_check
    assert collision_check["file_comparisons"] >= collision_check["shared_files"], collision_check
    archive_hashes = {}
    assert set(inputs["linker_scripts"]) == scripts
    script_hashes = {}
    for label in inputs["archives"]:
        repository = label.split("//", 1)[0].removeprefix("@@")
        source = root.resolve().parent.parent / repository / "data.tar.xz"
        assert source.is_file() and source.name == "data.tar.xz"
        archive_hashes[label] = hashlib.sha256(source.read_bytes()).hexdigest()
        if "_libc6-dev-" in label:
            with tarfile.open(source) as archive:
                available = {member.name.removeprefix("./"): member for member in archive.getmembers()}
                matching = scripts.intersection(available)
                assert len(matching) == 3, (label, matching)
                for script in sorted(matching):
                    assert script not in script_hashes, (label, script)
                    member = archive.extractfile(available[script])
                    assert member is not None
                    original = member.read().decode()
                    actual = (root / script).read_text()
                    notice, body = actual.split("\n", 1)
                    assert "AUTO-GENERATED" in notice and "DO NOT EDIT MANUALLY" in notice, script
                    references = re.findall(r"(=?)(/(?:lib/|lib64/|usr/lib/)[^\s)]+)", body)
                    assert references and all(prefix == "=" for prefix, _ in references), script
                    restored = body
                    for _, path in references:
                        restored = restored.replace("=" + path, path)
                    assert restored == original, f"unexpected change to Debian linker script: {script}"
                    paths = inputs["linker_scripts"][script]
                    assert paths and all((root / path.lstrip("/")).is_file() for path in paths)
                    assert set(paths) == {path for _, path in references}, script
                    script_hashes[script] = hashlib.sha256(actual.encode()).hexdigest()
    assert set(script_hashes) == scripts
    return {
        "sysroot_architectures": inputs["architectures"],
        "sysroot_tree_sha256": tree_hash.hexdigest(),
        "elf_machines": elf_machines,
        "source_archive_sha256": archive_hashes,
        "linker_script_sha256": script_hashes,
        "collision_check": collision_check,
    }


def run_program(loader, libraries, binary, expected, shared_library=None):
    search = [libraries]
    if shared_library:
        search.append(shared_library.parent)
    invocation = [str(loader), "--library-path", ":".join(map(str, search)), str(binary)]
    assert subprocess.check_output(invocation, text=True) == expected
    loaded = subprocess.check_output([str(loader), "--list", *invocation[1:]], text=True)
    loaded_paths = []
    for line in loaded.splitlines():
        if "=>" in line:
            resolved = Path(line.split("=>", 1)[1].strip().split(" (", 1)[0]).resolve()
            allowed = resolved.is_relative_to(libraries.resolve())
            if shared_library:
                allowed = allowed or resolved == shared_library.resolve()
            assert allowed, line
            loaded_paths.append(resolved)
    if shared_library:
        assert shared_library.resolve() in loaded_paths, loaded
    return loaded


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
    cpu = platform.machine()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[cpu]
    triplet, machine, loader_name = ARCHITECTURES[architecture]
    sysroot_label = "@sonic_sysroot//root:sysroot"
    receipts = {}
    programs = {}
    sysroot_directories = set()
    for compiler, toolchain in (
        ("gcc", f"@gcc_toolchains//:host_{cpu}_gcc_14.2.0"),
        ("clang", "@llvm_toolchain//:all"),
    ):
        evidence = artifacts / compiler
        evidence.mkdir(exist_ok=True)

        def bazel(command, *arguments):
            # `info execution_root` does not analyze the consumer's targets and
            # need not resolve its apparent platform/toolchain repository names.
            target_options = [] if command == "info" else [
                f"--host_platform=@sonic_build_infra//platforms:{cpu}_trixie",
                f"--platforms=@sonic_build_infra//platforms:{cpu}_trixie",
                f"--extra_toolchains={toolchain}",
            ]
            return subprocess.check_output(
                [args.bazel, *args.startup_option, command, *args.bazel_option,
                 *target_options, *arguments],
                cwd=consumer, text=True,
            ).strip()

        # Audit before executing build actions. These probes produce executables
        # and static/shared libraries, never Debian packages.
        graph_text = bazel("aquery", "--output=jsonproto", "deps(set(//:c_probe //:probe //:readelf //:objcopy //:native_position_independent_test))")
        (evidence / "actions.json").write_text(graph_text + "\n")
        actions, compiles, sysroot_path = audit_graph(graph_text, compiler, architecture)
        graph = json.loads(graph_text)
        paths = artifact_paths(graph)
        assert any(action["mnemonic"] == "CppArchive" for action in actions), "missing native archive action"
        bazel("build", "//:c_probe", "//:probe")
        execution_root = Path(bazel("info", "execution_root"))
        root = execution_root / sysroot_path
        # The directory target declares every file for GCC's header checks.
        # Its package directory is the root also consumed by LLVM's API.
        declared_root = execution_root / bazel(
            "cquery", "--output=starlark",
            "--starlark:expr=target.label.workspace_root + '/' + target.label.package",
            sysroot_label,
        )
        assert root.resolve() == declared_root.resolve(), (root, declared_root)
        sysroot_directories.add(str(root.resolve()))
        tree_receipt = validate_tree(root)
        libraries = root / f"usr/lib/{triplet}"
        loader = libraries / loader_name

        def output(label):
            files = bazel("cquery", "--output=files", label).splitlines()
            assert len(files) == 1, files
            return execution_root / files[0]

        # A different toolchain can rebuild the same bazel-out paths. Preserve
        # each compiler's bytes before selecting the other compiler, so the
        # cross-compiler check cannot accidentally test one build twice.
        saved = evidence / "outputs"
        saved.mkdir(exist_ok=True)
        binaries = {}
        binary_hashes = {}
        for name in ("c_probe", "probe", "libshared_probe.so"):
            destination = saved / name
            shutil.copy2(output("//:" + name), destination)
            header = destination.read_bytes()[:20]
            assert header[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", header, 18)[0] == machine, destination
            binaries[name] = destination
            binary_hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
        c_binary = binaries["c_probe"]
        cpp_binary = binaries["probe"]
        shared_library = binaries["libshared_probe.so"]
        archive = evidence / "libgenerated_value.a"
        shutil.copy2(output("//:generated_value"), archive)
        headers = subprocess.check_output(["readelf", "--file-header", str(archive)], text=True, env=dict(os.environ, LC_ALL="C"))
        expected_machine = {62: "Advanced Micro Devices X86-64", 183: "AArch64"}[machine]
        archive_machines = re.findall(r"Machine:\s+([^\n]+)", headers)
        assert archive_machines and set(archive_machines) == {expected_machine}, headers
        (evidence / "libgenerated_value.a-elf.txt").write_text(headers)
        dynamic = subprocess.check_output(
            ["readelf", "--dynamic", str(shared_library)], text=True,
            env=dict(os.environ, LC_ALL="C"),
        )
        assert re.search(r"\(SONAME\).*\[" + re.escape(shared_library.name) + r"\]", dynamic), dynamic
        (evidence / "shared-library-dynamic.txt").write_text(dynamic)
        loaded = {
            "c": run_program(loader, libraries, c_binary, "SONiC C sysroot 8\n"),
            "cpp": run_program(loader, libraries, cpp_binary, "SONiC shared sysroot 42 8\n", shared_library),
        }
        if compiler == "clang":
            compile_args = compiles[0]
            llvm_root = (execution_root / compile_args[compile_args.index("-resource-dir") + 1]).parents[2]
            compiler_path = llvm_root / "bin/clang"
        else:
            compiler_path = execution_root / compiles[0][0]
        version = subprocess.check_output([str(compiler_path), "--version"], cwd=execution_root, text=True)
        assert ("clang version 19.1.7" if compiler == "clang" else "14.2.0") in version, version
        receipts[compiler] = {
            "_generated": {"notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.", "generator": "toolchains/sysroot/tests/validate.py"},
            "architecture": architecture,
            "compiler_version": version,
            "sysroot_label": sysroot_label,
            "sysroot_directory": str(root.resolve()),
            "action_count": len(actions),
            "deb_outputs": [],
            "compile_arguments": compiles,
            "link_arguments": [item["arguments"] for item in actions if item["mnemonic"] == "CppLink"],
            "loaded_libraries": loaded,
            "binary_sha256": binary_hashes,
            "archive_sha256": {archive.name: hashlib.sha256(archive.read_bytes()).hexdigest()},
            **tree_receipt,
        }
        if compiler == "gcc":
            receipts[compiler]["debian_compiler"] = validate_debian_compiler(
                execution_root, compiler_path, graph, paths, evidence,
                execution_architecture=architecture, target_architecture=architecture,
            )
            receipts[compiler]["public_binutils"] = validate_public_binutils(
                bazel, c_binary, machine, "//:native_position_independent_test", evidence / "binutils",
            )
        lockfile = consumer / "MODULE.bazel.lock"
        assert lockfile.is_file(), "Keep Bazel's generated module lockfile enabled for validation evidence"
        shutil.copy2(lockfile, evidence / "MODULE.bazel.lock")
        receipts[compiler]["module_lockfile_sha256"] = hashlib.sha256(lockfile.read_bytes()).hexdigest()
        programs[compiler] = (cpp_binary, shared_library)
        (evidence / "validation.json").write_text(json.dumps(receipts[compiler], indent=2) + "\n")
        print(f"Validated {compiler} on native {architecture} using the combined AMD64/ARM64 sysroot", flush=True)

    assert len(sysroot_directories) == 1, sysroot_directories
    assert receipts["gcc"]["source_archive_sha256"] == receipts["clang"]["source_archive_sha256"]
    assert receipts["gcc"]["sysroot_tree_sha256"] == receipts["clang"]["sysroot_tree_sha256"]
    # Also cross the compiler boundary: GCC's caller uses Clang's shared library
    # and vice versa, exercising C++ exceptions and threads with the common ABI.
    cross_compiler = {}
    for caller, library in (("gcc", "clang"), ("clang", "gcc")):
        cross_compiler[f"{caller}_caller_{library}_library"] = run_program(
            loader, libraries, programs[caller][0], "SONiC shared sysroot 42 8\n", programs[library][1],
        )
    receipt = {
        "_generated": {"notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.", "generator": "toolchains/sysroot/tests/validate.py"},
        "architecture": architecture,
        "sysroot_label": sysroot_label,
        "sysroot_directory": sysroot_directories.pop(),
        "sysroot_architectures": ["amd64", "arm64"],
        "compilers": receipts,
        "cross_compiler_loaded_libraries": cross_compiler,
    }
    (artifacts / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"GCC and Clang share one AMD64/ARM64 sysroot directory on native {architecture}")


if __name__ == "__main__":
    main()
