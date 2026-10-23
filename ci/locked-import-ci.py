#!/usr/bin/env python3
"""Exercise this checkout's Distroless patch and tests on a native host."""
import argparse
import base64
import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

REGISTRY_REF = "main"
# Publication provenance; source.json and every payload remain integrity checked.
REGISTRY_LANDED_COMMIT = "e9c04070eadcf08e9d37b3a7e510c5481e99f432"  # registry PR #96
VERSION = "0.9.4.sonic.10"
SOURCE_SHA256 = "c05789b197d4e4754d1c3fd4d4ee0283c0b33e2deddaae4c0654be1248b6b8fe"
TARGETS = ["@rules_distroless//apt/tests:locked_import/" + name
           for name in ["closure", "invalid_lock", "selection"]] + [
    "@rules_distroless//registry_ci/locked_import:archive_contract_test"]


def checked(data, integrity):
    actual = "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()
    if actual != integrity:
        raise ValueError(f"Integrity mismatch: expected {integrity}, got {actual}")
    return data


def safe_path(name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe recipe path: {name}")
    return path


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def prepare(source, work, recipe_dir=None, archive=None):
    """Verify the prerequisite, then apply only this checkout's feature bytes."""
    work.mkdir(parents=True, exist_ok=False)
    recipe_url = ("https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/"
                  f"{REGISTRY_REF}/modules/rules_distroless/{VERSION}/")
    def recipe(name):
        safe_path(name)
        return (recipe_dir / name).read_bytes() if recipe_dir else fetch(recipe_url + name)
    description = recipe("source.json")
    if hashlib.sha256(description).hexdigest() != SOURCE_SHA256:
        raise ValueError("Prerequisite source.json does not match the reviewed recipe")
    spec = json.loads(description)
    tar_bytes = checked(archive.read_bytes() if archive else fetch(spec["url"]), spec["integrity"])
    archive_path = work / "upstream.tar.gz"
    archive_path.write_bytes(tar_bytes)
    with tarfile.open(archive_path) as bundle:
        bundle.extractall(work / "source", filter="data")
    module = work / "source" / safe_path(spec["strip_prefix"])
    for name, digest in spec["patches"].items():
        path = work / "patches" / safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(checked(recipe("patches/" + name), digest))
        subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", f"-p{spec['patch_strip']}",
                        "-i", str(path)], cwd=module, check=True)
    for name, digest in spec["overlay"].items():
        path = module / safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(checked(recipe("overlay/" + name), digest))
    if (module / "MODULE.bazel").read_bytes() != recipe("MODULE.bazel"):
        raise ValueError("Prerequisite patched MODULE differs from its registry declaration")
    patch = source / "patches/0001-import-apt-lock.patch"
    subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch)],
                   cwd=module, check=True)
    shutil.copytree(source / "tests/locked_import", module / "registry_ci/locked_import")
    with (module / "MODULE.bazel").open("a") as stream:
        stream.write('\n# Source-checkout locked-import regression fixtures.\n'
                     'locked_apt_ci = use_extension("//apt:extensions.bzl", "apt")\n'
                     'locked_apt_ci.import_lock(lock = "//registry_ci/locked_import:apt.lock.json")\n'
                     'use_repo(locked_apt_ci, "locked_import_ci", "locked_import_ci_copy")\n')
    consumer = work / "consumer"
    consumer.mkdir()
    (consumer / "MODULE.bazel").write_text(
        'module(name = "locked_import_source_ci")\n'
        f'bazel_dep(name = "rules_distroless", version = "{VERSION}")\n'
        'local_path_override(module_name = "rules_distroless", path = '
        + json.dumps(str(module)) + ')\n')
    (consumer / "BUILD.bazel").write_text("")
    (consumer / ".bazelversion").write_text("8.5.1\n")
    return consumer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", required=True, choices=["amd64", "arm64"])
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--recipe-dir", type=Path, help="Optional verified recipe cache")
    parser.add_argument("--archive", type=Path, help="Optional integrity-checked upstream cache")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "third_party/rules_distroless"
    root = source.parents[1]
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[args.architecture]
    if platform.machine() != machine or subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True).strip() != args.architecture:
        raise ValueError("Requested architecture must match the native host and userspace")
    inputs = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(source.rglob("*")) if p.is_file()}
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    receipt = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
               "architecture": args.architecture, "machine": platform.machine(),
               "source_inputs": inputs, "prerequisite_registry_ref": REGISTRY_REF,
               "prerequisite_registry_landed_commit": REGISTRY_LANDED_COMMIT,
               "prerequisite_version": VERSION, "source_json_sha256": SOURCE_SHA256,
               "targets": TARGETS, "result": "incomplete"}
    try:
        consumer = prepare(source, args.work_dir.resolve(),
                           args.recipe_dir.resolve() if args.recipe_dir else None,
                           args.archive.resolve() if args.archive else None)
        if args.prepare_only:
            receipt["result"] = "prepared; native tests not executed"
            return
        command = [args.bazel, "--ignore_all_rc_files", "--output_user_root=" + str(args.work_dir.resolve() / "bazel"),
                   "test", "--lockfile_mode=update", "--registry=https://bcr.bazel.build",
                   "--test_output=errors", "--build_event_json_file=" + str(artifacts / "test-bep.json"),
                   "--platforms=@rules_distroless//registry_ci:" + args.architecture, *TARGETS]
        receipt["command"] = command
        with (artifacts / "test.log").open("w") as output:
            result = subprocess.run(command, cwd=consumer, stdout=output, stderr=subprocess.STDOUT)
        lock = consumer / "MODULE.bazel.lock"
        if lock.exists():
            shutil.copy2(lock, artifacts / lock.name)
        result.check_returncode()
        if not lock.is_file():
            raise ValueError("Bazel did not generate its resolution lock")
        summaries = {}
        for line in (artifacts / "test-bep.json").read_text().splitlines():
            event = json.loads(line)
            identifier = event.get("id", {}).get("testSummary")
            if identifier:
                label = identifier["label"].replace("@@rules_distroless+//", "@rules_distroless//")
                summaries[label] = event["testSummary"]
        if set(summaries) != set(TARGETS) or any(
                x.get("overallStatus") != "PASSED" for x in summaries.values()):
            raise ValueError("Expected source regression tests did not all pass")
        receipt.update(result="passed", test_summaries=summaries)
    finally:
        receipt["source_inputs_unchanged"] = all(
            hashlib.sha256((root / name).read_bytes()).hexdigest() == digest for name, digest in inputs.items())
        (artifacts / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if not receipt["source_inputs_unchanged"]:
            raise ValueError("Source inputs changed during validation")


if __name__ == "__main__":
    main()
