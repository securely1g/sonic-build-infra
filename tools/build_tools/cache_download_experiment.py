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
COMMON_VERSION = "0.0.4-bab24c3409a1e5839f249d49903ded95b0b41be3"
COMMON_MODULE_SHA256 = "ef40c2278f46c88b257e9437b453428b9017ebd640a55af8b26c852d546e70c7"
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
    decoder, selected, buffer, eof = json.JSONDecoder(), [], "", False
    # Bazel writes concatenated objects. Bound memory by one record rather than
    # retaining a potentially multi-gigabyte execution log in a Python string.
    with path.open() as stream:
        while True:
            buffer = buffer.lstrip()
            if not buffer and eof:
                break
            try:
                record, offset = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if eof:
                    raise ValueError(f"Truncated or invalid execution record in {path.name}")
                if len(buffer) >= 64 * 1024 * 1024:
                    raise ValueError(f"Execution record exceeds 64 MiB in {path.name}")
                chunk = stream.read(1024 * 1024)
                buffer += chunk
                eof = not chunk
                continue
            buffer = buffer[offset:]
            if record.get("mnemonic") in ("PrepareBuildToolsRootfs", "DebianRuntimeMetadata", "TestRunner"):
                selected.append({key: record.get(key) for key in
                                 ("mnemonic", "targetLabel", "listedOutputs", "runner", "cacheHit", "status", "exitCode")})
    return selected


def private_execution_root(consumer, user_root):
    output_link = consumer / "bazel-out"
    assert output_link.is_symlink(), "Missing consumer bazel-out symlink"
    output = output_link.resolve(strict=True)
    private_root = user_root.resolve(strict=True)
    execution_root = output.parent
    assert output.is_dir() and output.name == "bazel-out", output
    assert execution_root.is_relative_to(private_root), (execution_root, private_root)
    assert execution_root.parent.name == "execroot", execution_root
    return execution_root


def module_identity(path):
    declaration = re.search(r"\bmodule\s*\((.*?)\n\)", path.read_text(), re.DOTALL)
    assert declaration is not None, "Missing module declaration in " + str(path)
    values = {}
    for field in ("name", "version"):
        match = re.search(r'^\s*' + field + r'\s*=\s*"([^\"]+)"', declaration[1], re.MULTILINE)
        assert match is not None, "Missing module " + field + " in " + str(path)
        values[field] = match[1]
    return values


def selected_module_receipt(mapping, execution_root, infra, directory, expected_infra_sha):
    assert isinstance(mapping, dict), "Expected root repository mapping"
    external = execution_root.parent.parent / "external"
    receipt = {"scope": "Root repository mapping and selected infra/Common modules; not a full graph",
               "registry": REGISTRY, "modules": {}}
    for apparent, expected_name, expected_version, expected_sha in (
            ("sonic_build_infra", "sonic-build-infra", module_identity(infra / "MODULE.bazel")["version"], expected_infra_sha),
            ("sonic_swss_common", "sonic-swss-common", COMMON_VERSION, COMMON_MODULE_SHA256)):
        canonical = mapping.get(apparent)
        assert isinstance(canonical, str) and re.fullmatch(r"[A-Za-z0-9_+~.\-]+", canonical), canonical
        assert canonical not in (".", ".."), canonical
        repository = external / canonical
        observed = repository / "MODULE.bazel"
        shutil.copyfile(observed, directory / (apparent + ".MODULE.bazel"))
        identity = module_identity(observed)
        observed_sha = sha(observed)
        row = {"canonical_repository": canonical, "repository_path": str(repository),
               "resolved_repository_path": str(repository.resolve(strict=True)),
               "identity": identity, "sha256": observed_sha,
               "expected_sha256": expected_sha}
        receipt["modules"][apparent] = row
        # Retain observed bytes and partial receipt before rejecting a mismatch.
        dump(directory / "selected-modules.json", receipt)
        assert identity == {"name": expected_name, "version": expected_version}, row
        assert observed_sha == expected_sha, row
        if apparent == "sonic_build_infra":
            assert repository.resolve(strict=True) == infra.resolve(strict=True), row
    receipt["verified"] = True
    dump(directory / "selected-modules.json", receipt)
    return receipt


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
    source_files = ["MODULE.bazel", "tools/build_tools/prepare_rootfs.py", "tools/build_tools/prepare_runtime.py",
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
                  "disk_free_before_bytes": shutil.disk_usage(scratch).free,
                  "collection_errors": []}
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
        def bazel(name, command, arguments, *, json_output=False, execution=False, events=False):
            flags = [*common, "--disk_cache=" + str(cache), *download]
            if execution:
                flags += ["--execution_log_json_file=" + str(directory / (name + ".spawns.json")),
                          "--execution_log_sort=false"]
            if execution or events:
                flags.append("--build_event_json_file=" + str(directory / (name + ".events.jsonl")))
            return run(name, [*startup, command, *flags, *arguments], json_output=json_output)

        def collect(name, operation):
            try:
                return operation()
            except Exception as error:
                result["collection_errors"].append({"step": name, "error": str(error)})
                return None

        try:
            if mode != "seed-eager":
                assert (scratch / "seed-cache").is_dir(), "Seed did not populate a cache"
                assert run("copy-seed-cache", ["cp", "-a", "--reflink=auto", str(scratch / "seed-cache"), str(cache)]) == 0
            # Each measured mode writes only to its private copy of the same seed.
            # This is the original consumer job sequence; runtime is never a top-level build target.
            result["canonical_exit"] = bazel("canonical", "build", targets["raw_build"], events=True)
            result["tests_exit"] = bazel("tests", "test", ["--nocache_test_results", "--test_output=errors", *targets["service_free_tests"]], execution=True)
            result["reachability_exit"] = bazel("compile-actions", "aquery", ["--output=jsonproto", "--noinclude_artifacts", "--include_commandline", 'mnemonic("CppCompile", deps(//:canonical_outputs))'], json_output=True)
            if result["reachability_exit"] == 0:
                with (directory / "compile-actions.json").open() as stream:
                    result["reachability_exit"] = run("source-reachability", ["python3", "bazel/ci/check_source_reachability.py", "--manifest", "bazel/ci/expected_no_vpp_sources.json", "--output", str(directory / "source-reachability.json")], stdin=stream)
            result["metadata_exit"] = bazel("metadata", "build", ["//:runtime_packages.json", *targets["package_build"]], execution=True)
            codes = [result[name] for name in ("canonical_exit", "tests_exit", "reachability_exit", "metadata_exit")]
            result["command_status"] = "passed" if all(code == 0 for code in codes) else "failed"

            def collect_runtime_paths():
                result["runtime_actions_exit"] = bazel("runtime-actions", "aquery", ["--output=jsonproto", 'mnemonic("PrepareBuildToolsRootfs", deps(//:runtime_packages.json))'], json_output=True)
                assert result["runtime_actions_exit"] == 0
                paths = rootfs_paths(json.loads((directory / "runtime-actions.json").read_text()))
                assert paths and all(re.fullmatch(PATTERN, path) for path in paths), paths
                result["runtime_paths"] = paths
                execution_root = private_execution_root(consumer, user_root)
                result["execution_root"] = str(execution_root)
                dump(directory / "runtime-link-states.json", {path: links(execution_root / path) for path in paths})

            collect("runtime-paths", collect_runtime_paths)

            def collect_metadata():
                assert bazel("metadata-output", "cquery", ["--output=files", "//:runtime_packages.json"]) == 0
                lines = (directory / "metadata-output.log").read_text().splitlines()
                names = [line for line in lines if line.startswith("bazel-out/") and line.endswith("runtime_packages.json")]
                assert len(names) == 1, names
                source = private_execution_root(consumer, user_root) / names[0]
                shutil.copyfile(source, directory / "runtime_packages.json")
                metadata = json.loads(source.read_text())
                result["metadata_sha256"] = sha(source)
                return metadata

            if result["metadata_exit"] == 0:
                metadata = collect("metadata-output", collect_metadata)
                if mode == "seed-eager" and metadata is not None:
                    seed_metadata = metadata
                result["matches_seed_metadata"] = seed_metadata is not None and metadata == seed_metadata

            def collect_mapping():
                # Bazel 8.5.1 mod graph evaluates unrelated unused extensions.
                # Mapping avoids that inspector; verify the actual selected
                # repositories and their MODULE bytes rather than a graph string.
                result["repo_mapping_exit"] = run("repo-mapping", [*startup, "mod", "dump_repo_mapping", "", "--lockfile_mode=update", "--override_module=sonic-build-infra=" + str(infra)], json_output=True)
                assert result["repo_mapping_exit"] == 0, "Root repository mapping was not captured"
                mapping = json.loads((directory / "repo-mapping.json").read_text())
                receipt = selected_module_receipt(mapping, private_execution_root(consumer, user_root),
                                                  infra, directory, source_hashes["MODULE.bazel"])
                result["selected_modules_verified"] = receipt["verified"]

            collect("selected-modules", collect_mapping)
        except Exception as error:
            result.update(status="inconclusive", error=str(error))
        finally:
            # Always preserve available receipts, even when another evidence
            # query fails. Such failures cannot turn a successful command into
            # a proven mitigation, or hide the original command's exit status.
            for path in directory.glob("*.spawns.json"):
                parsed = collect(path.name, lambda path=path: execution_records(path))
                if parsed is not None:
                    records.extend(parsed)
            dump(directory / "relevant-executions.json", records)
            runtime_label = "//tools/build_tools:kernel_runtime_" + args.architecture
            restored = [record for record in records if record["mnemonic"] == "PrepareBuildToolsRootfs"
                        and str(record["targetLabel"]).endswith(runtime_label)]
            result["runtime_cache_hits"] = restored
            result["cache_proven"] = bool(restored) and all(record["cacheHit"] is True and record["runner"] == "disk cache hit" for record in restored)
            metadata_runs = [record for record in records if record["mnemonic"] == "DebianRuntimeMetadata"
                             and record["targetLabel"] in ("//:runtime_packages.json", "@@//:runtime_packages.json", "@//:runtime_packages.json")]
            result["metadata_execution"] = metadata_runs
            result["metadata_reexecuted"] = bool(metadata_runs) and all(record["cacheHit"] is False for record in metadata_runs)
            if (consumer / "MODULE.bazel.lock").is_file():
                collect("retain-lock", lambda: shutil.copyfile(consumer / "MODULE.bazel.lock", directory / "MODULE.bazel.lock"))
            result["shutdown_exit"] = collect("shutdown", lambda: run("shutdown", [*startup, "shutdown"]))
            # Write a preliminary receipt before cleanup/compression so any
            # interruption still leaves the original command outcomes intact.
            dump(directory / "result.json", result)
            if result["shutdown_exit"] == 0 and user_root.is_dir() and user_root.parent == scratch:
                collect("remove-private-output", lambda: shutil.rmtree(user_root))
                result["private_output_removed_after_evidence"] = not user_root.exists()
            if mode != "seed-eager" and result["shutdown_exit"] == 0 and cache.is_dir() and cache.parent == scratch:
                collect("remove-private-cache", lambda: shutil.rmtree(cache))
                result["private_cache_removed_after_evidence"] = not cache.exists()
            for path in directory.glob("*.spawns.json"):
                def compress(path=path):
                    with path.open("rb") as source, gzip.open(str(path) + ".gz", "wb") as destination:
                        shutil.copyfileobj(source, destination)
                    path.unlink()
                collect("compress-" + path.name, compress)
            result["status"] = result.get("command_status", "inconclusive")
            if result["status"] == "passed":
                cache_valid = (bool(restored) and any(record["cacheHit"] is False for record in restored)) if mode == "seed-eager" else result["cache_proven"]
                if result["collection_errors"] or not (cache_valid and result["metadata_reexecuted"] and result.get("matches_seed_metadata") and result.get("selected_modules_verified")):
                    result["status"] = "inconclusive"
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
