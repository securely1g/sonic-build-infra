"""Verify Debian compiler payloads and their declared execution runtime."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import tarfile


def artifact_paths(graph):
    fragments = {item["id"]: item for item in graph["pathFragments"]}

    def path(identifier):
        item = fragments[identifier]
        prefix = path(item["parentId"]) + "/" if item.get("parentId") else ""
        return prefix + item["label"]

    return {item["id"]: path(item["pathFragmentId"]) for item in graph["artifacts"]}


def validate_debian_compiler(execution_root, compiler, graph, paths, evidence,
                             execution_architecture, target_architecture):
    repository = compiler.parent.parent
    root = repository.resolve()
    metadata = json.loads((repository / "inputs.json").read_text())
    assert metadata["format_version"] == 1
    assert metadata["execution_architecture"] == execution_architecture
    assert metadata["target_architecture"] == target_architecture
    host = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    assert host == execution_architecture, (host, execution_architecture)
    machine = {"amd64": 62, "arm64": 183}[execution_architecture]
    # Debian's AMD64 package names use x86-64; ELF/tool paths use x86_64.
    package_prefix = {"amd64": "x86-64-linux-gnu", "arm64": "aarch64-linux-gnu"}[target_architecture]
    assert "bootlin" not in json.dumps(metadata).lower()
    archive_labels = [item["archive"] for item in metadata["packages"]]
    assert len(archive_labels) == len(set(archive_labels))
    for package in ("gcc-14-" + package_prefix, "g++-14-" + package_prefix, "binutils-" + package_prefix):
        assert any(package.replace("+", "-") in label for label in archive_labels), package
    assert all("-" + execution_architecture + "_" in label for label in archive_labels), archive_labels

    def private_path(relative):
        value = repository / relative
        assert not Path(relative).is_absolute() and value.resolve().is_relative_to(root), relative
        return value

    def native_elf(path):
        with path.open("rb") as source:
            header = source.read(20)
        assert header[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", header, 18)[0] == machine, path

    def relative(path):
        return path.resolve().relative_to(root).as_posix()

    loader = private_path(metadata["loader"])
    native_elf(loader)
    libraries = [private_path(item) for item in metadata["library_directories"]]
    assert libraries and all(item.is_dir() for item in libraries)
    tools = {name: private_path(path) for name, path in metadata["tools"].items()}
    required_tools = {"gcc", "g++", "gcc-ar", "ar", "as", "ld", "cc1", "cc1plus", "collect2"}
    assert required_tools.issubset(tools), tools
    expected_payloads = {relative(loader)}
    runtime = {}
    for name, tool in tools.items():
        native_elf(tool)
        expected_payloads.add(relative(tool))
        listing = subprocess.check_output([
            str(loader), "--inhibit-cache", "--library-path", ":".join(map(str, libraries)), "--list", str(tool),
        ], text=True)
        (evidence / f"debian-{name}-runtime.txt").write_text(listing)
        resolved = set()
        for line in listing.splitlines():
            if "=>" not in line:
                continue
            path = Path(line.split("=>", 1)[1].strip().split(" (", 1)[0])
            assert path.is_file() and path.resolve().is_relative_to(root), (name, line)
            native_elf(path)
            resolved.add(relative(path))
        assert resolved, (name, listing)
        runtime[name] = resolved
        expected_payloads.update(resolved)

    # Compare the retained original ELF files with the pinned Debian payloads,
    # rather than trusting only the compiler's version string or a manifest.
    archives = {}
    origins = {}
    for label in archive_labels:
        repo_name, member_path = label.removeprefix("@@").split("//", 1)
        archive = root.parent / repo_name / member_path.replace(":", "/").lstrip("/")
        assert archive.name == "data.tar.xz" and archive.is_file(), label
        archives[label] = hashlib.sha256(archive.read_bytes()).hexdigest()
        with tarfile.open(archive) as contents:
            for member in contents:
                name = member.name.removeprefix("./")
                if name not in expected_payloads or not member.isfile():
                    continue
                original = contents.extractfile(member).read()
                actual = (root / name).read_bytes()
                assert actual == original, (name, label)
                origins[name] = {"archive": label, "sha256": hashlib.sha256(actual).hexdigest()}
    assert expected_payloads == set(origins), sorted(expected_payloads - set(origins))

    # Observe the public wrapper itself using its private libraries. The shell
    # interpreter starts first and can use the machine's libc; the compiler's
    # initialization must include its separately declared Debian runtime.
    environment = dict(os.environ, LD_DEBUG="libs", LC_ALL="C")
    for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_DEBUG_OUTPUT"):
        environment.pop(name, None)
    observed = subprocess.run([str(compiler), "--version"], env=environment, text=True, capture_output=True, check=True)
    assert "Debian" in observed.stdout and "14.2.0" in observed.stdout, observed.stdout
    (evidence / "debian-gcc-wrapper-runtime.txt").write_text(observed.stdout + observed.stderr)
    initialized = {
        relative(Path(path.strip())) for path in re.findall(r"calling init:\s+([^\n]+)", observed.stderr)
        if Path(path.strip()).resolve().is_relative_to(root)
    }
    assert runtime["gcc"] - {relative(loader)} <= initialized, (runtime["gcc"], initialized)

    # Check actual action inputs too: a compatible private runtime is useful
    # only if it is available inside Bazel's compilation/link/archive sandbox.
    dependency_sets = {item["id"]: item for item in graph["depSetOfFiles"]}

    def inputs(identifiers):
        result = set()
        for identifier in identifiers:
            item = dependency_sets[identifier]
            result.update(item.get("directArtifactIds", []))
            result.update(inputs(item.get("transitiveDepSetIds", [])))
        return result

    repository_input = repository.relative_to(execution_root).as_posix()
    checked = set()
    for action in graph["actions"]:
        arguments = action.get("arguments", [])
        if not arguments or not arguments[0].startswith(repository_input + "/bin/"):
            continue
        kind = action["mnemonic"]
        if kind == "CppCompile":
            used = {"g++", "cc1plus", "as"} if any(arg.endswith(".cc") for arg in arguments) else {"gcc", "cc1", "as"}
        elif kind == "CppLink":
            used = {"g++", "collect2", "ld"}
        elif kind == "CppArchive":
            used = {"gcc-ar", "ar"}
        else:
            continue
        required = {relative(loader)}
        for name in used:
            required.add(relative(tools[name]))
            required.update(runtime[name])
        declared = {paths[item] for item in inputs(action["inputDepSetIds"])}
        assert arguments[0] in declared, (kind, arguments[0])
        missing = {repository_input + "/" + item for item in required} - declared
        assert not missing, (kind, sorted(missing))
        checked.add(kind)
    assert checked == {"CppCompile", "CppLink", "CppArchive"}, checked
    receipt = {
        "_generated": {"notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.", "generator": "toolchains/sysroot/tests/validate_debian_compiler.py"},
        "execution_architecture": execution_architecture,
        "target_architecture": target_architecture,
        "source_archive_sha256": archives,
        "original_payloads": origins,
        "tool_runtime": {name: sorted(files) for name, files in runtime.items()},
        "wrapper_initialized_runtime": sorted(initialized),
        "audited_actions": sorted(checked),
    }
    (evidence / "debian-compiler-validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def validate_public_binutils(bazel, binary, machine, position_independent_test, evidence):
    """Exercise the public tool targets, whose runfiles must carry the runtime."""
    evidence.mkdir(exist_ok=True)
    version = bazel("run", "//:readelf", "--", "--version")
    assert "Debian" in version and "2.44" in version, version
    headers = bazel("run", "//:readelf", "--", "--file-header", str(binary))
    architecture = {62: "Advanced Micro Devices X86-64", 183: "AArch64"}[machine]
    assert re.search(r"Machine:\s+" + re.escape(architecture), headers), headers
    (evidence / "readelf.txt").write_text(version + "\n" + headers + "\n")
    copied = evidence / (binary.name + ".without-debug")
    bazel("run", "//:objcopy", "--", "--strip-debug", str(binary), str(copied))
    header = copied.read_bytes()[:20]
    assert header[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", header, 18)[0] == machine, copied
    bazel("test", "--test_output=errors", position_independent_test)
    return {
        "readelf_version": version,
        "objcopy_output_sha256": hashlib.sha256(copied.read_bytes()).hexdigest(),
        "elf_machine": machine,
        "position_independent_test": "passed",
    }
