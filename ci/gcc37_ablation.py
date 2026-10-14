#!/usr/bin/env python3
"""Measure all PR37 GCC flag combinations without changing its regression."""

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


SOURCE = "6d56f968788c03fc502667680f536ea66ef9440f"
PARENT = "3b9e506e305c76874ab8071568d22206720840d1"
ROOT = Path(__file__).resolve().parents[1]
ARGS = "toolchains/gcc/args/platform_dependent_args.bzl"
BUILD = "toolchains/gcc/gcc.BUILD"
TARGET = "//toolchains/gcc/tests:foreign_link_test"
CASES = ("111", "000", "100", "010", "001", "110", "101", "011")
GROUPS = {
    "A": "Four libc6/libgcc-s1 /lib -> /usr/lib search paths",
    "B": "Normal libgcc-dev -L directory (old relative workaround retained)",
    "C": "Declared compiler_programs aliases and their -B directory",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def source(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT).decode()


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def replace_once(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after, 1)


def variant(bits):
    args, build = source(SOURCE, ARGS), source(SOURCE, BUILD)
    if bits[0] == "0":
        for package in ("libc6", "libgcc-s1"):
            for prefix in ("", "../../../../../../../"):
                before = '"' + prefix + "{{" + package + "}}/usr/lib/{multiarch}\""
                args = replace_once(args, before, before.replace("/usr/lib/", "/lib/"))
    if bits[1] == "0":
        args = replace_once(args, '    "-L",\n    "{{libgcc-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}",\n', "")
    if bits[2] == "0":
        args = replace_once(args, '    available["gcc-programs"] = gcc_repo + "//:compiler_programs"\n', "")
        args = replace_once(args, "    # Headers and programs supplied by the GCC distribution rather than apt.",
                            "    # The one directory that comes from the GCC distribution rather than from apt.")
        start = args.index('    struct(\n        suffix = "_programs",')
        end = args.index('    struct(\n        suffix = "_c_includes",', start)
        args = args[:start] + args[end:]
        start = build.index("# GCC invokes these programs itself, including from foreign build directories.\n")
        end = build.index("# Various supporting files needed to run the linker.\n", start)
        build = build[:start] + build[end:]
    return {ARGS: args, BUILD: build}


def run(command, log):
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    print(f"{log.name}: exit {result.returncode}", flush=True)
    return result.returncode


def audit(graph_path):
    graph = json.loads(graph_path.read_text())
    fragments = {f["id"]: f for f in graph["pathFragments"]}

    def path(identifier):
        node = fragments[identifier]
        return (path(node["parentId"]) + "/" if node.get("parentId") else "") + node["label"]

    artifacts = {a["id"]: path(a["pathFragmentId"]) for a in graph["artifacts"]}
    actions = graph["actions"]
    assert actions, "empty action graph"
    forbidden = []
    for action in actions:
        command = " ".join(action.get("arguments", []))
        outputs = [artifacts[i] for i in action.get("outputIds", [])]
        if (any(p.endswith(".deb") for p in outputs)
                or re.search(r"(?:^|[/\s])(?:make|gmake|dpkg-buildpackage)(?:\s|$)", command)
                or ("dpkg-deb" in command and re.search(r"(?:--build|(?:^|\s)-b(?:\s|$))", command))):
            forbidden.append(action.get("mnemonic"))
    assert not forbidden, forbidden
    return {"actions": len(actions), "forbidden_packaging_actions": forbidden}


def execution_records(path):
    text = path.read_text()
    decoder = json.JSONDecoder()
    records = []
    while text.strip():
        text = text.lstrip()
        value, end = decoder.raw_decode(text)
        records.extend(value if isinstance(value, list) else [value])
        text = text[end:]
    return [{k: r.get(k) for k in ("mnemonic", "runner", "cacheHit", "exitCode", "status")}
            for r in records if r.get("mnemonic") == "CcConfigureMakeRule"]


def collect(case, output_base, new_sandboxes, test_reached):
    allowed = {"Configure.log", "build_script.sh", "wrapper_build_script.sh", "probe",
               "probe.map", "probe.c", "assembler.txt", "linker.txt", "linker-execution.txt"}
    roots = []
    if test_reached:
        roots.append(("installed", ROOT / "bazel-bin/toolchains/gcc/tests/foreign_probe"))
        roots.append(("generated", ROOT / "bazel-bin/toolchains/gcc/tests/foreign_probe_foreign_cc"))
        testlogs = ROOT / "bazel-testlogs/toolchains/gcc/tests/foreign_link_test"
        if testlogs.is_dir():
            shutil.copytree(testlogs, case / "testlogs", symlinks=False, dirs_exist_ok=True)
    for index, sandbox in enumerate(sorted(new_sandboxes)):
        for path in sandbox.glob("execroot/*/bazel-out/*/bin/toolchains/gcc/tests"):
            roots.append((f"sandbox-{index}", path))
    log = (case / "test-command.log").read_text()
    for index, match in enumerate(re.findall(r"Build Dir:\s*([^\r\n]+)", log)):
        path = Path(match.strip().strip("'\"`"))
        if path.is_dir() and path.resolve().is_relative_to(output_base.resolve()):
            roots.append((f"failed-build-{index}", path))
    for name, root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.name in allowed and path.is_file():
                dest = case / name / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)


def observations(case):
    def receipt(name):
        paths = sorted(case.rglob(name))
        contents = {path.read_bytes() for path in paths}
        assert len(contents) <= 1, f"mixed receipts: {name}"
        return paths[0].read_text().strip() if paths else None

    result = {"runtime_exit": None, "elf_machine": None, "selected_tools_declared": None,
              "backend_matches_declared_linker": None, "library_inputs_declared": None}
    binaries = sorted(case.rglob("probe"))
    if binaries:
        assert len({digest(p.read_bytes()) for p in binaries}) == 1, "mixed executables"
        binary = binaries[0]
        elf = binary.read_bytes()
        if len(elf) >= 20 and elf[:6] == b"\x7fELF\x02\x01":
            result["elf_machine"] = struct.unpack_from("<H", elf, 18)[0]
            result["native_elf"] = result["elf_machine"] == {"x86_64": 62, "aarch64": 183}[platform.machine()]
            try:
                execution = subprocess.run([str(binary)], capture_output=True, text=True)
                result["runtime_exit"] = execution.returncode
                result["runtime_stdout"] = execution.stdout
                result["runtime_stderr"] = execution.stderr
            except OSError as error:
                result["runtime_error"] = str(error)
        else:
            result["binary_format_error"] = "probe is not a complete ELF64 little-endian executable"
    assembler, linker = receipt("assembler.txt"), receipt("linker.txt")
    result.update(assembler=assembler, linker=linker)
    if assembler is not None and linker is not None:
        result["selected_tools_declared"] = all(v.startswith("/") and "/external/" in v
                                                and not v.startswith(("/usr/", "/lib/"))
                                                for v in (assembler, linker))
    backend = receipt("linker-execution.txt")
    if backend is not None and linker is not None:
        commands = [line.split()[0] for line in backend.splitlines() if line.startswith("/")]
        result["backend_commands"] = commands
        result["backend_matches_declared_linker"] = (linker in commands and bool(commands)
                                                     and not any(c.startswith(("/usr/", "/lib/")) for c in commands))
    link_map = receipt("probe.map")
    if link_map is not None:
        inputs = re.findall(r"^LOAD (.+)$", link_map, re.MULTILINE)
        library_inputs = {}
        for library, package in (("libgcc_s.so", "libgcc-14-dev"), ("libgcc_s.so.1", "libgcc-s1"), ("libc.so.6", "libc6")):
            paths = [p for p in inputs if p.endswith("/" + library)]
            library_inputs[library] = {"paths": paths, "declared": bool(paths) and all(
                "/external/" in p and package in p and "/usr/lib/" in p for p in paths)}
        result["library_inputs"] = library_inputs
        result["library_inputs_declared"] = (all(v["declared"] for v in library_inputs.values())
                                             and not any(p.startswith(("/usr/", "/lib/", "/lib64/")) for p in inputs))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    options = parser.parse_args()
    originals = {path: source(SOURCE, path) for path in (ARGS, BUILD)}
    assert variant("111") == originals
    assert variant("000") == {path: source(PARENT, path) for path in originals}
    for path, content in originals.items():
        assert (ROOT / path).read_text() == content, f"unexpected initial edit: {path}"
    if options.verify_only:
        print(json.dumps({bits: {p: digest(v) for p, v in variant(bits).items()} for bits in CASES}, indent=2))
        return

    artifacts = ROOT / "gcc37-artifacts"
    artifacts.mkdir(exist_ok=True)
    machine = platform.machine()
    assert machine in ("x86_64", "aarch64")
    assert subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip() == {"x86_64": "amd64", "aarch64": "arm64"}[machine]
    assert "VERSION_CODENAME=trixie" in Path("/etc/os-release").read_text()
    assert subprocess.check_output(["bazel", "--version"], text=True).strip() == "bazel 8.5.1"
    output_base = Path(os.environ["RUNNER_TEMP"]) / "gcc37-bazel"
    bazel = ["bazel", "--ignore_all_rc_files", f"--output_base={output_base}"]
    native = f"//platforms:{machine}_trixie"
    flags = [f"--platforms={native}", f"--host_platform={native}", "--jobs=4", "--lockfile_mode=update",
             "--registry=https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main", "--registry=https://bcr.bazel.build/",
             "--remote_cache=", "--disk_cache=", "--remote_executor=", "--noremote_accept_cached", "--color=no", "--curses=no"]
    fixtures = ["MODULE.bazel", "toolchains/gcc/tests/BUILD.bazel", "toolchains/gcc/tests/project/configure",
                "toolchains/gcc/tests/project/probe.c", "toolchains/gcc/tests/foreign_link_test.py"]
    immutable = {p: digest((ROOT / p).read_bytes()) for p in fixtures}
    for path in fixtures:
        assert (ROOT / path).read_bytes() == source(SOURCE, path).encode(), path
    save(artifacts / "environment.json", {"source": SOURCE, "production_baseline": PARENT, "experiment": git("rev-parse", "HEAD"),
         "machine": machine, "uname": list(os.uname()), "image": os.environ.get("CI_IMAGE"), "bazel": "8.5.1",
         "groups": GROUPS, "fixture_hashes": immutable, "command_prefix": bazel, "flags": flags})
    shutil.copy2("/etc/os-release", artifacts / "os-release")
    run(["dpkg-query", "-W"], artifacts / "host-packages.txt")
    results = []
    try:
        for bits in CASES:
            print(f"::group::Case {bits}: A={bits[0]} B={bits[1]} C={bits[2]}", flush=True)
            case = artifacts / bits
            case.mkdir()
            changes = variant(bits)
            for path, content in changes.items():
                (ROOT / path).write_text(content)
                destination = case / "source" / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content)
            assert {p: digest((ROOT / p).read_bytes()) for p in fixtures} == immutable
            save(case / "inputs.json", {"case": bits, "groups": GROUPS, "production_hashes": {p: digest(v) for p, v in changes.items()}, "fixture_hashes": immutable})
            with (case / "actions.json").open("w") as output, (case / "aquery.log").open("w") as error:
                query = subprocess.run(bazel + ["aquery", *flags, "--output=jsonproto", f"deps({TARGET})"], cwd=ROOT, stdout=output, stderr=error)
            assert query.returncode == 0, f"case {bits}: action analysis failed; see aquery.log"
            save(case / "action-audit.json", audit(case / "actions.json"))
            sandbox_root = output_base / "sandbox/processwrapper-sandbox"
            before = set(sandbox_root.iterdir()) if sandbox_root.is_dir() else set()
            command = bazel + ["test", *flags, "--spawn_strategy=processwrapper-sandbox", "--sandbox_debug", "--verbose_failures",
                               "--nocache_test_results", "--test_output=all", f"--build_event_json_file={case / 'bep.json'}",
                               f"--execution_log_json_file={case / 'execution.json'}", TARGET]
            save(case / "command.json", command)
            rc = run(command, case / "test-command.log")
            events = [json.loads(line) for line in (case / "bep.json").read_text().splitlines()]
            summaries = [e["testSummary"] for e in events if "testSummary" in e]
            test_results = [e["testResult"] for e in events if "testResult" in e]
            after = set(sandbox_root.iterdir()) if sandbox_root.is_dir() else set()
            # A FAILED_TO_BUILD summary does not establish fresh test outputs.
            # Only a testResult event permits reading installed outputs/testlogs.
            collect(case, output_base, after - before, bool(test_results))
            record = {"case": bits, "bazel_exit": rc, "test_summary": summaries,
                      "test_results": test_results, "observations": observations(case)}
            if (case / "execution.json").exists():
                record["foreign_actions"] = execution_records(case / "execution.json")
            else:
                record["foreign_actions"] = []
            save(case / "result.json", record)
            results.append(record)
            save(artifacts / "summary.json", results)
            assert record["foreign_actions"], f"case {bits}: no executed configure action; infrastructure failure"
            assert any(a.get("runner") == "processwrapper-sandbox" and not a.get("cacheHit", False) for a in record["foreign_actions"]), "configure did not execute in a fresh sandbox"
            if bits == "111":
                assert rc == 0, "full PR control failed; stop before interpreting ablations"
                observed = record["observations"]
                assert observed.get("native_elf") and observed["runtime_exit"] == 0, "missing control runtime evidence"
                assert all(observed[key] for key in ("selected_tools_declared", "backend_matches_declared_linker", "library_inputs_declared")), "missing control provenance evidence"
            if (ROOT / "MODULE.bazel.lock").exists():
                shutil.copy2(ROOT / "MODULE.bazel.lock", case / "MODULE.bazel.lock")
            print(json.dumps({k: v for k, v in record.items() if k != "foreign_actions"}), flush=True)
            print("::endgroup::", flush=True)
    finally:
        for path, content in originals.items():
            (ROOT / path).write_text(content)
    assert len(results) == 8
    hashes = {str(p.relative_to(artifacts)): digest(p.read_bytes()) for p in artifacts.rglob("*") if p.is_file()}
    save(artifacts / "sha256.json", hashes)


if __name__ == "__main__":
    main()
