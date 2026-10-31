#!/usr/bin/env python3
"""Export and validate the shared build_tools APT lock during manual refreshes.

The resolver is rules_distroless. This helper accepts only the observed Bazel
8.5.1 resolver record, projects one dependency set, and checks that the landed
lock importer reads the same package archives and graph before publishing it.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apt"))
from sonic_apt.inputs import package_keys  # noqa: E402


BAZEL_VERSION = "8.5.1"
BAZEL_LOCK_VERSION = 24
RULES_VERSION = "0.9.4.sonic.13"
DEPENDENCY_SET = "build_tools"
ARCHITECTURES = {"amd64", "arm64", "armhf"}
REGISTRY = "https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main"
BCR = "https://bcr.bazel.build/"
APT_EXTENSION = "@@rules_distroless+//apt:extensions.bzl%apt"
HUB_RULE = "@@rules_distroless+//apt/private:translate_dependency_set.bzl%translate_dependency_set"
DEB_RULE = "@@rules_distroless+//apt/private:deb_import.bzl%deb_import"
RULES_REGISTRY_HASHES = {
    REGISTRY + "/modules/rules_distroless/" + RULES_VERSION + "/MODULE.bazel":
        "41ece285cc92ed3bed31c52b86108acc684dcc9783b6ff97ed82d9947db5f821",
    REGISTRY + "/modules/rules_distroless/" + RULES_VERSION + "/source.json":
        "4a7b35be74dae0ef3480c7aaeabc61adef736d30d0b36c6cba1ee41234ec9e97",
}
PACKAGE_FIELDS = {
    "architecture", "depends_on", "filename", "name", "section", "sha256", "size", "suite", "version",
}
SOURCE_FIELDS = {"architectures", "components", "types", "uris"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def write_json(path, value):
    """Replace an output only after its complete JSON has been written."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_bazel(bazel):
    executable = shutil.which(bazel)
    require(executable is not None, "Bazel executable was not found: " + bazel)
    result = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True)
    require(result.stdout.strip() == "bazel " + BAZEL_VERSION,
            "lock preparation requires Bazel " + BAZEL_VERSION + "; found " + result.stdout.strip())
    return executable


def hub_record(module_lock, imported):
    """Read only the pinned Bazel/rules_distroless record shape we validated."""
    require(module_lock.get("lockFileVersion") == BAZEL_LOCK_VERSION,
            "expected Bazel 8.5.1 MODULE.bazel.lock version 24")
    hashes = module_lock.get("registryFileHashes")
    require(isinstance(hashes, dict), "Bazel lock lacks registryFileHashes")
    selected = {key: value for key, value in hashes.items()
                if "/modules/rules_distroless/" in key and key.endswith(("/MODULE.bazel", "/source.json"))}
    require(selected == RULES_REGISTRY_HASHES,
            "Bazel lock does not select the reviewed rules_distroless 0.9.4.sonic.13 recipe from canonical main")
    extensions = module_lock.get("moduleExtensions")
    require(isinstance(extensions, dict) and APT_EXTENSION in extensions, "Bazel lock lacks the expected APT extension")
    apt = extensions[APT_EXTENSION]
    require(isinstance(apt, dict) and set(apt) == {"general"}, "unexpected APT extension configuration record")
    general = apt["general"]
    require(isinstance(general, dict) and isinstance(general.get("generatedRepoSpecs"), dict),
            "APT extension lacks generatedRepoSpecs")
    repositories = general["generatedRepoSpecs"]
    require(DEPENDENCY_SET in repositories, "APT extension did not declare build_tools")
    hub = repositories[DEPENDENCY_SET]
    require(isinstance(hub, dict) and hub.get("repoRuleId") == HUB_RULE, "unexpected build_tools repository rule")
    attributes = hub.get("attributes")
    expected = {"depset_name", "lock_content"}
    if imported:
        expected |= {"repo_prefix", "payload_only"}
    require(isinstance(attributes, dict) and set(attributes) == expected, "unexpected build_tools repository attributes")
    require(attributes["depset_name"] == DEPENDENCY_SET and isinstance(attributes["lock_content"], str),
            "unexpected build_tools repository identity")
    if imported:
        require(attributes["repo_prefix"] == "locked_import_build_tools_" and attributes["payload_only"] is True,
                "build_tools was not read through apt.import_lock")
    return json.loads(attributes["lock_content"]), repositories


def validate_source(name, source):
    require(isinstance(source, dict) and set(source) == SOURCE_FIELDS, "unexpected APT source shape: " + name)
    for field in SOURCE_FIELDS:
        require(isinstance(source[field], list) and (source[field] or field == "types") and
                all(isinstance(item, str) and item for item in source[field]), "invalid APT source " + field + ": " + name)
    for uri in source["uris"]:
        parsed = urlsplit(uri)
        require(parsed.scheme in {"http", "https"} and parsed.netloc and parsed.username is None and
                parsed.password is None and not parsed.query and not parsed.fragment,
                "invalid APT source URI: " + uri)


def validate_package(key, package, sources):
    require(isinstance(package, dict) and set(package) == PACKAGE_FIELDS, "unexpected APT package shape: " + key)
    for field in {"architecture", "filename", "name", "section", "sha256", "suite", "version"}:
        require(isinstance(package[field], str) and package[field], "invalid APT package " + field + ": " + key)
    require(re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", package["name"]) is not None,
            "unsafe APT package name: " + key)
    require(re.fullmatch(r"[0-9a-f]{64}", package["sha256"]) is not None, "invalid APT package SHA256: " + key)
    filename = PurePosixPath(package["filename"])
    require(filename.parts and filename.suffix == ".deb" and not filename.is_absolute() and
            ".." not in filename.parts and "\\" not in package["filename"],
            "unsafe APT package filename: " + key)
    require(type(package["size"]) is int and package["size"] > 0, "invalid APT package size: " + key)
    require(package["suite"] in sources, "APT package lacks its source: " + key)
    require(key.split("=", 1)[0].rsplit(":", 1)[0] == "/" + package["suite"] + "/" + package["name"],
            "APT package key does not match its source identity: " + key)
    require(isinstance(package["depends_on"], list) and
            all(isinstance(item, str) and item for item in package["depends_on"]) and
            len(set(package["depends_on"])) == len(package["depends_on"]), "invalid APT dependency keys: " + key)


def project_candidate(module_lock):
    """Select the resolver's complete build_tools graph without resolving it."""
    resolved, _ = hub_record(module_lock, imported=False)
    require(isinstance(resolved, dict) and set(resolved) == {"dependency_sets", "facts", "packages", "sources", "version"},
            "unexpected resolver lock_content shape")
    require(resolved["version"] == 2, "expected a version-2 APT resolver lock")
    require(all(isinstance(resolved[field], dict) for field in {"dependency_sets", "facts", "packages", "sources"}),
            "invalid APT resolver lock mappings")
    definition = resolved["dependency_sets"].get(DEPENDENCY_SET)
    require(isinstance(definition, dict) and set(definition) == {"sets"} and isinstance(definition["sets"], dict),
            "resolver lock lacks the build_tools definition")
    require(set(definition["sets"]) == ARCHITECTURES, "build_tools must retain amd64, arm64 and armhf selections")
    for name, source in resolved["sources"].items():
        validate_source(name, source)

    selected = set()
    for architecture in sorted(ARCHITECTURES):
        roots = definition["sets"][architecture]
        require(isinstance(roots, dict) and roots and all(isinstance(key, str) and isinstance(value, str) and value
                                                         for key, value in roots.items()),
                "invalid build_tools roots for " + architecture)
        try:
            keys = package_keys(resolved, DEPENDENCY_SET, architecture)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid build_tools graph for " + architecture + ": " + str(error)) from error
        names = [resolved["packages"][key]["name"] for key in keys]
        require(len(names) == len(set(names)), "build_tools contains two versions of one package for " + architecture)
        selected.update(keys)

    packages = {}
    for key in sorted(selected):
        package = resolved["packages"][key]
        validate_package(key, package, resolved["sources"])
        require(set(package["depends_on"]) <= selected, "APT dependency is outside build_tools: " + key)
        packages[key] = dict(package, depends_on=sorted(package["depends_on"]))
    return {
        "_generated": ("AUTO-GENERATED by sonic-build-infra/tools/build_tools/export_apt_lock.py from Bazel 8.5.1 "
                       "rules_distroless 0.9.4.sonic.13 resolver output; do not edit manually."),
        "version": 2,
        "sources": resolved["sources"],
        "packages": packages,
        "dependency_sets": {DEPENDENCY_SET: definition},
    }


def reachable(packages, roots):
    pending, seen = list(roots), set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        require(key in packages, "APT dependency is missing: " + key)
        seen.add(key)
        pending.extend(packages[key]["depends_on"])
    return seen


def package_urls(lock, package):
    return [uri.rstrip("/") + "/" + package["filename"] for uri in lock["sources"][package["suite"]]["uris"]]


def package_repository(key):
    """Use the exact rules_distroless 0.9.4 util.sanitize mapping guarded above."""
    sanitized = key.removeprefix("/").replace("+", "-").replace(":", "-").replace("~", "_").replace("/", "_").replace("=", "_")
    return "locked_import_build_tools_" + sanitized


def validate_import(lock, module_lock):
    """Check the upstream import's expanded graph and original archive inputs."""
    imported, repositories = hub_record(module_lock, imported=True)
    require(lock.get("version") == 2 and set(lock.get("dependency_sets", {})) == {DEPENDENCY_SET},
            "expected one canonical version-2 build_tools lock")
    require(set(lock["dependency_sets"][DEPENDENCY_SET].get("sets", {})) == ARCHITECTURES and
            isinstance(lock.get("packages"), dict) and isinstance(lock.get("sources"), dict),
            "canonical build_tools lock lacks its complete architecture graph")
    require(imported.get("sources") == lock.get("sources"), "imported APT sources differ from the canonical lock")
    expected_sets, selected = {}, set()
    for architecture in sorted(ARCHITECTURES):
        keys = package_keys(lock, DEPENDENCY_SET, architecture)
        expected_sets[architecture] = {key.rsplit("=", 1)[0]: lock["packages"][key]["version"] for key in keys}
        selected.update(keys)
    require(imported.get("dependency_sets") == {DEPENDENCY_SET: {"sets": expected_sets}},
            "imported APT dependency sets differ from the canonical closure")
    require(isinstance(imported.get("packages"), dict) and set(imported["packages"]) == selected,
            "imported APT package keys differ from the canonical closure")
    for key in sorted(selected):
        expected = dict(lock["packages"][key], depends_on=sorted(reachable(lock["packages"], [key]) - {key}))
        actual = imported["packages"][key]
        require(actual == expected, "imported APT package differs from the canonical lock: " + key)

    expected_archives = {}
    for key in sorted(selected):
        name = package_repository(key)
        require(name not in expected_archives, "locked package repository names collide: " + key)
        package = lock["packages"][key]
        expected_archives[name] = {
            "target_name": name,
            "urls": package_urls(lock, package),
            "sha256": package["sha256"],
            "mergedusr": False,
            "depends_on": sorted(reachable(lock["packages"], [key]) - {key}),
            "package_name": package["name"],
            "payload_only": True,
            "repo_prefix": "locked_import_build_tools_",
        }
    actual_archives = {name: repository for name, repository in repositories.items()
                       if name.startswith("locked_import_build_tools_")}
    require(set(actual_archives) == set(expected_archives), "original DEB repository names differ from the canonical lock")
    for name, repository in actual_archives.items():
        require(repository.get("repoRuleId") == DEB_RULE, "unexpected locked package repository: " + name)
        require(repository.get("attributes") == expected_archives[name],
                "original DEB repository inputs differ from the canonical lock: " + name)
    return {architecture: {"roots": len(lock["dependency_sets"][DEPENDENCY_SET]["sets"][architecture]),
                           "packages": len(expected_sets[architecture])} for architecture in sorted(ARCHITECTURES)}


def import_roundtrip(lock, bazel, repository_cache):
    with tempfile.TemporaryDirectory(prefix="build-tools-lock-import-") as directory:
        root = Path(directory)
        write_json(root / "apt.lock.json", lock)
        (root / ".bazelversion").write_text(BAZEL_VERSION + "\n")
        (root / "BUILD.bazel").write_text('exports_files(["apt.lock.json"])\n')
        (root / "MODULE.bazel").write_text(
            'module(name = "sonic_build_tools_lock_import_check", version = "0.0.0")\n'
            'bazel_dep(name = "rules_distroless", version = "' + RULES_VERSION + '")\n'
            'apt = use_extension("@rules_distroless//apt:extensions.bzl", "apt")\n'
            'apt.import_lock(lock = "//:apt.lock.json", dependency_sets = ["build_tools"])\n'
            'use_repo(apt, "build_tools")\n'
        )
        command = [bazel, "--batch", "--ignore_all_rc_files", "--output_user_root=" + str(root / "output"),
                   "query", "--registry=" + REGISTRY, "--registry=" + BCR, "--lockfile_mode=update"]
        if repository_cache:
            command.append("--repository_cache=" + str(Path(repository_cache).resolve()))
        command.append("@build_tools//:packages")
        subprocess.run(command, cwd=root, check=True)
        module_lock_path = root / "MODULE.bazel.lock"
        counts = validate_import(lock, read_json(module_lock_path))
        return counts, sha256(module_lock_path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    export = subparsers.add_parser("export", help="export a resolver candidate after an upstream import metadata check")
    export.add_argument("--module-lock", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--repository-cache", type=Path)
    validate = subparsers.add_parser("validate", help="check an ordinary build's imported graph against its canonical lock")
    validate.add_argument("--module-lock", required=True, type=Path)
    validate.add_argument("--lock", required=True, type=Path)
    for command in (export, validate):
        command.add_argument("--bazel", default="bazel")
        command.add_argument("--receipt", type=Path)
    args = parser.parse_args()

    bazel = check_bazel(args.bazel)
    module_lock_bytes = args.module_lock.read_bytes()
    module_lock = json.loads(module_lock_bytes)
    if args.operation == "export":
        lock = project_candidate(module_lock)
        counts, import_sha256 = import_roundtrip(lock, bazel, args.repository_cache)
        write_json(args.output, lock)
        lock_bytes = args.output.read_bytes()
    else:
        lock_bytes = args.lock.read_bytes()
        lock = json.loads(lock_bytes)
        counts = validate_import(lock, module_lock)
        import_sha256 = sha256(module_lock_bytes)
    receipt = {
        "schema": 1,
        "operation": args.operation,
        "validation": "upstream apt.import_lock metadata roundtrip passed",
        "bazel_version": BAZEL_VERSION,
        "rules_distroless_version": RULES_VERSION,
        "input_module_lock_sha256": sha256(module_lock_bytes),
        "canonical_lock_sha256": sha256(lock_bytes),
        "import_module_lock_sha256": import_sha256,
        "architectures": counts,
    }
    if args.receipt:
        write_json(args.receipt, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit("build_tools lock preparation failed: " + str(error)) from error
