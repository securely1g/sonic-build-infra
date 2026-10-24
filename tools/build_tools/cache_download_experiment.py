#!/usr/bin/env python3
"""Temporary native Sairedis experiment; not a production workflow or rule."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

BASELINE = "011a91d8030d9b8e72fec9b3163e40f51d72ba7e"
CONSUMER = "baaafcfb9422ad55577717f4e7c342ba5293a54a"
REGISTRY = "dba318c8a9702fb588d351225a42df782454a514"
PATTERN = r"(^|.*/)tools/build_tools/kernel_runtime_(amd64|arm64)(/.*)?$"
MODES = [
    ("seed-eager", ["--remote_download_all"]),
    ("default", ["--remote_download_outputs=toplevel"]),
    ("all", ["--remote_download_all"]),
    ("regex", ["--remote_download_outputs=toplevel", "--remote_download_regex=" + PATTERN]),
]


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def execution_records(path):
    if not path.is_file():
        return []
    text = path.read_text()
    decoder, offset, selected = json.JSONDecoder(), 0, []
    while offset < len(text):
        record, offset = decoder.raw_decode(text, offset)
        if record.get("mnemonic") in ("PrepareBuildToolsRootfs", "DebianRuntimeMetadata", "TestRunner"):
            selected.append({key: record.get(key) for key in
                             ("mnemonic", "targetLabel", "listedOutputs", "runner", "cacheHit", "status", "exitCode")})
        while offset < len(text) and text[offset].isspace():
            offset += 1
    return selected


def rootfs_paths(data):
    fragments = {item["id"]: item for item in data.get("pathFragments", [])}
    def path(identifier):
        item = fragments[identifier]
        prefix = path(item["parentId"]) + "/" if item.get("parentId") else ""
        return prefix + item["label"]
    artifacts = {item["id"]: path(item["pathFragmentId"]) for item in data.get("artifacts", [])}
    return sorted({artifacts[identifier] for action in data.get("actions", [])
                   if action.get("mnemonic") == "PrepareBuildToolsRootfs"
                   for identifier in action.get("outputIds", [])})


def links(root):
    result = {}
    for relative in ("lib", "bin", "usr/lib/x86_64-linux-gnu/perl/5.40",
                     "usr/lib/aarch64-linux-gnu/perl/5.40"):
        p = root / relative
        result[relative] = {"exists": p.exists(), "is_directory": p.is_dir(),
                            "is_symlink": p.is_symlink(),
                            "link_target": os.readlink(p) if p.is_symlink() else None,
                            "bits_linux_exists": (p / "bits/linux").is_dir() if "/perl/" in relative else None}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("infra", "consumer", "output", "scratch"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--architecture", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    infra, consumer, output, scratch = (getattr(args, name).resolve() for name in
                                        ("infra", "consumer", "output", "scratch"))
    output.mkdir(parents=True, exist_ok=True)
    assert not scratch.exists(), "Experiment requires a fresh dedicated disk cache"
    scratch.mkdir(parents=True)
    assert git(consumer, "rev-parse", "HEAD") == CONSUMER
    assert not git(consumer, "diff", "--name-only", "HEAD")
    assert not git(infra, "diff", "--name-only", "HEAD")
    assert not (consumer / "MODULE.bazel.lock").exists()
    assert not git(consumer, "ls-files", "MODULE.bazel.lock")
    expected = {"amd64": "x86_64", "arm64": "aarch64"}[args.architecture]
    assert platform.machine() == expected
    assert subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip() == args.architecture
    assert platform.freedesktop_os_release()["VERSION_CODENAME"] == "trixie"
    changed = set(git(infra, "diff", "--name-only", BASELINE, "HEAD").splitlines())
    assert changed <= {".github/workflows/declared-build-tools.yml", "tools/build_tools/cache_download_experiment.py"}, changed
    assert "materialize_directory_aliases" not in (infra / "tools/build_tools/prepare_rootfs.py").read_text()
    assert '\"/lib/$multiarch\", \"/usr/lib/$multiarch\"' in (infra / "tools/build_tools/kernel_debian.py").read_text()
    source_files = ["tools/build_tools/prepare_rootfs.py", "tools/build_tools/prepare_runtime.py",
                    "tools/build_tools/kernel_debian.py", "oci/runtime_packages.py"]
    source_hashes = {name: sha(infra / name) for name in source_files}
    for name in source_files:
        assert (infra / name).read_bytes() == subprocess.check_output(["git", "-C", str(infra), "show", BASELINE + ":" + name])
    main_registry = "https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main"
    rc = (consumer / ".bazelrc").read_text()
    assert rc.count(main_registry) == 1
    effective = output / "effective.bazelrc"
    effective.write_text(rc.replace(main_registry, main_registry.removesuffix("main") + REGISTRY))
    dump(output / "inputs.json", {"baseline": BASELINE, "infra_head": git(infra, "rev-parse", "HEAD"),
         "consumer": CONSUMER, "registry": REGISTRY, "architecture": args.architecture,
         "machine": platform.machine(), "image": args.image, "source_hashes": source_hashes,
         "regex": PATTERN, "seed_policy": "Eager seed, same unnormalized source and action inputs as measured modes"})
    (output / "os-release").write_text(Path("/etc/os-release").read_text())
    with (output / "host-packages.txt").open("w") as stream:
        subprocess.run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"], stdout=stream, check=True)
    targets = json.loads((consumer / "bazel/ci/required-targets.json").read_text())
    common = ["--jobs=4", "--lockfile_mode=update", "--repository_cache=" + str(scratch / "repositories"),
              "--override_module=sonic-build-infra=" + str(infra),
              "--modify_execution_info=^DebianRuntimeMetadata$=+no-cache"]
    if args.architecture == "arm64":
        common.append("--config=aarch64")
    results, seed_metadata = [], None
    for mode, download in MODES:
        directory, user_root = output / mode, scratch / ("outputs-" + mode)
        directory.mkdir()
        assert not user_root.exists()
        (consumer / "MODULE.bazel.lock").unlink(missing_ok=True)
        startup = ["bazel", "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
                   "--bazelrc=" + str(effective), "--output_user_root=" + str(user_root)]
        commands, records = [], []
        cache = scratch / ("seed-cache" if mode == "seed-eager" else "cache-" + mode)
        result = {"mode": mode, "download_flags": download, "status": "running",
                  "disk_free_before_bytes": shutil.disk_usage(scratch).free}
        started = time.monotonic()
        def run(name, command, *, json_output=False, stdin=None):
            destination = directory / (name + (".json" if json_output else ".log"))
            began = time.monotonic()
            with destination.open("w") as stdout, (directory / (name + ".stderr.log")).open("w") as stderr:
                completed = subprocess.run(command, cwd=consumer, stdin=stdin, stdout=stdout,
                                           stderr=stderr if json_output else subprocess.STDOUT)
            commands.append({"name": name, "command": command, "exit_code": completed.returncode,
                             "elapsed_seconds": time.monotonic() - began})
            dump(directory / "commands.json", commands)
            print(mode, name, completed.returncode, flush=True)
            return completed.returncode
        def bazel(name, command, arguments, *, json_output=False, execution=False):
            flags = [*common, "--disk_cache=" + str(cache), *download]
            if execution:
                flags += ["--execution_log_json_file=" + str(directory / (name + ".spawns.json")),
                          "--build_event_json_file=" + str(directory / (name + ".events.jsonl"))]
            return run(name, [*startup, command, *flags, *arguments], json_output=json_output)
        try:
            if mode != "seed-eager":
                assert (scratch / "seed-cache").is_dir(), "Seed did not populate a cache"
                assert run("copy-seed-cache", ["cp", "-a", "--reflink=auto", str(scratch / "seed-cache"), str(cache)]) == 0
            # Each measured mode writes only to its private copy of the same seed.
            # This is the original consumer job sequence; runtime is never a top-level build target.
            result["canonical_exit"] = bazel("canonical", "build", targets["raw_build"], execution=True)
            result["tests_exit"] = bazel("tests", "test", ["--nocache_test_results", "--test_output=errors", *targets["service_free_tests"]], execution=True)
            result["reachability_exit"] = bazel("compile-actions", "aquery", ["--output=jsonproto", "--noinclude_artifacts", "--include_commandline", 'mnemonic("CppCompile", deps(//:canonical_outputs))'], json_output=True)
            if result["reachability_exit"] == 0:
                with (directory / "compile-actions.json").open() as stream:
                    result["reachability_exit"] = run("source-reachability", ["python3", "bazel/ci/check_source_reachability.py", "--manifest", "bazel/ci/expected_no_vpp_sources.json", "--output", str(directory / "source-reachability.json")], stdin=stream)
            result["metadata_exit"] = bazel("metadata", "build", ["//:runtime_packages.json", *targets["package_build"]], execution=True)
            assert bazel("runtime-actions", "aquery", ["--output=jsonproto", 'mnemonic("PrepareBuildToolsRootfs", deps(//:runtime_packages.json))'], json_output=True) == 0
            paths = rootfs_paths(json.loads((directory / "runtime-actions.json").read_text()))
            assert paths and all(re.fullmatch(PATTERN, path) for path in paths), paths
            result["runtime_paths"] = paths
            execution_root = Path(subprocess.check_output([*startup, "info", *common, "execution_root"], cwd=consumer, text=True).strip())
            dump(directory / "runtime-link-states.json", {path: links(execution_root / path) for path in paths})
            for path in directory.glob("*.spawns.json"):
                records.extend(execution_records(path))
            dump(directory / "relevant-executions.json", records)
            restored = [record for record in records if record["mnemonic"] == "PrepareBuildToolsRootfs"]
            result["runtime_cache_hits"] = restored
            result["cache_proven"] = bool(restored) and all(record["cacheHit"] is True and record["runner"] == "disk cache hit" for record in restored)
            metadata_runs = [record for record in records if record["mnemonic"] == "DebianRuntimeMetadata"
                             and record["targetLabel"] in ("//:runtime_packages.json", "@@//:runtime_packages.json", "@//:runtime_packages.json")]
            result["metadata_execution"] = metadata_runs
            result["metadata_reexecuted"] = bool(metadata_runs) and all(record["cacheHit"] is False for record in metadata_runs)
            if result["metadata_exit"] == 0:
                assert bazel("metadata-output", "cquery", ["--output=files", "//:runtime_packages.json"]) == 0
                lines = (directory / "metadata-output.log").read_text().splitlines()
                names = [line for line in lines if line.startswith("bazel-out/") and line.endswith("runtime_packages.json")]
                assert len(names) == 1, names
                source = execution_root / names[0]
                shutil.copyfile(source, directory / "runtime_packages.json")
                metadata = json.loads(source.read_text())
                result["metadata_sha256"] = sha(source)
                if mode == "seed-eager":
                    seed_metadata = metadata
                result["matches_seed_metadata"] = seed_metadata is not None and metadata == seed_metadata
            result["module_graph_exit"] = run("module-graph", [*startup, "mod", "graph", "--output=json", "--lockfile_mode=update", "--override_module=sonic-build-infra=" + str(infra)], json_output=True)
            assert result["module_graph_exit"] == 0, "Module graph was not captured"
            graph = json.loads((directory / "module-graph.json").read_text())
            assert "sonic-build-infra" in json.dumps(graph), "Missing overridden infrastructure in graph"
            codes = [result[name] for name in ("canonical_exit", "tests_exit", "reachability_exit", "metadata_exit")]
            result["status"] = "passed" if all(code == 0 for code in codes) else "failed"
            if result["status"] == "passed":
                assert result["metadata_reexecuted"] and result["matches_seed_metadata"], result
                assert (bool(restored) and any(record["cacheHit"] is False for record in restored)) if mode == "seed-eager" else result["cache_proven"], result
        except Exception as error:
            result.update(status="inconclusive", error=str(error))
        finally:
            if (consumer / "MODULE.bazel.lock").is_file():
                shutil.copyfile(consumer / "MODULE.bazel.lock", directory / "MODULE.bazel.lock")
            result["shutdown_exit"] = run("shutdown", [*startup, "shutdown"])
            if result["shutdown_exit"] == 0 and user_root.is_dir() and user_root.parent == scratch:
                shutil.rmtree(user_root)
                result["private_output_removed_after_evidence"] = True
            if mode != "seed-eager" and result["shutdown_exit"] == 0 and cache.is_dir() and cache.parent == scratch:
                shutil.rmtree(cache)
                result["private_cache_removed_after_evidence"] = True
            for path in directory.glob("*.spawns.json"):
                with path.open("rb") as source, gzip.open(str(path) + ".gz", "wb") as destination:
                    shutil.copyfileobj(source, destination)
                path.unlink()
            result["elapsed_seconds"] = time.monotonic() - started
            dump(directory / "result.json", result)
            results.append(result)
            dump(output / "summary.json", {"architecture": args.architecture, "baseline": BASELINE,
                 "consumer": CONSUMER, "results": results, "workflow_success_means": "Experiment completed; inspect per-mode results, not this workflow color, to assess mitigation."})
    assert all(sha(infra / name) == value for name, value in source_hashes.items())
    assert not git(consumer, "diff", "--name-only", "HEAD")
    # A failing baseline is an expected observation. Missing evidence is not success.
    assert all(result["status"] != "inconclusive" for result in results), results


if __name__ == "__main__":
    main()
