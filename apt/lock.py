#!/usr/bin/env python3
"""Update or check the single APT production lock through Distroless.

The resolver is the selected rules_distroless extension. This tool supplies its
reviewed inputs, validates the exported graph, preserves unchanged package
checksums and metadata, and commits a complete result with an atomic rename.
Normal --check never resolves, downloads, or writes the lock.
"""

import argparse
from copy import deepcopy
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile


RESOLVER_VERSION = "0.9.4.sonic.26"
IDENTITY_FIELDS = ("name", "version", "architecture", "suite", "filename", "sha256")


def read_json(path):
    return json.loads(Path(path).read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def source_records(document):
    if document.get("version") != 1 or not isinstance(document.get("sources"), list):
        raise ValueError("source configuration must have version 1 and sources list")
    records = {}
    for entry in document["sources"]:
        for field in ("uris", "suites", "components", "architectures"):
            if not entry.get(field) or not isinstance(entry[field], list):
                raise ValueError("source configuration lacks " + field)
        for uri in entry["uris"]:
            if not isinstance(uri, str) or not uri:
                raise ValueError("source URI must be a nonempty string")
        aliases = entry.get("suite_keys", {})
        if not isinstance(aliases, dict) or any(key not in entry["suites"] for key in aliases):
            raise ValueError("suite_keys must map configured actual suites")
        origin = entry.get("origin", "")
        if not isinstance(origin, str) or (aliases and not origin):
            raise ValueError("suite_keys require a named origin")
        for actual_suite in entry["suites"]:
            suite = aliases.get(actual_suite, actual_suite)
            if not isinstance(suite, str) or not suite or "/" in suite:
                raise ValueError("logical suite key must be one nonempty path segment")
            record = {key: deepcopy(entry.get(key, [])) for key in ("uris", "components", "architectures", "types")}
            if origin:
                record.update(origin=origin, suite=actual_suite)
            if suite in records and records[suite] != record:
                raise ValueError("conflicting source configuration for suite " + suite)
            records[suite] = record
    return records


def validate_requests(document):
    if document.get("version") != 1 or not isinstance(document.get("dependency_sets"), dict):
        raise ValueError("requests must have version 1 and dependency_sets object")
    if not document["dependency_sets"]:
        raise ValueError("requests must select at least one dependency set")
    for name, request in document["dependency_sets"].items():
        if not name or not isinstance(request.get("packages"), list) or not request["packages"]:
            raise ValueError("dependency set lacks package requests: " + name)
        if any(not isinstance(package, str) or not package for package in request["packages"]):
            raise ValueError("invalid package request: " + name)
        if not isinstance(request.get("suites", []), list):
            raise ValueError("suites must be a list: " + name)
        if not isinstance(request.get("include_transitive", True), bool):
            raise ValueError("include_transitive must be boolean: " + name)


def package_coordinate(key):
    coordinate, version = key.rsplit("=", 1)
    suite_name, architecture = coordinate.rsplit(":", 1)
    suite, name = suite_name.strip("/").split("/", 1)
    if not all((suite, name, architecture, version)):
        raise ValueError("invalid package key: " + key)
    return suite, name, architecture, version


def reachable(document):
    packages = document["packages"]
    seen = set()
    pending = []
    for group, definition in document["dependency_sets"].items():
        if not isinstance(definition.get("sets"), dict) or not definition["sets"]:
            raise ValueError("dependency set has no architectures: " + group)
        for architecture, roots in definition["sets"].items():
            for key, version in roots.items():
                full = key + "=" + version
                if package_coordinate(full)[2] not in (architecture, "all"):
                    raise ValueError("root architecture mismatch: " + full)
                pending.append(full)
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        if key not in packages:
            raise ValueError("APT dependency is missing from lock: " + key)
        seen.add(key)
        pending.extend(packages[key]["depends_on"])
    return seen


def validate_collection_versions(document):
    """Reject competing binary identities for the same effective architecture."""
    for group, definition in document["dependency_sets"].items():
        for architecture, roots in definition["sets"].items():
            pending = [key + "=" + version for key, version in roots.items()]
            seen, names = set(), {}
            while pending:
                key = pending.pop()
                if key in seen:
                    continue
                seen.add(key)
                package = document["packages"][key]
                name = package["name"]
                # Architecture: all has the native dpkg architecture (Debian
                # Policy 5.6.34.1). Cross-architecture tool inputs stay distinct.
                binary_architecture = package["architecture"]
                effective_architecture = architecture if binary_architecture == "all" else binary_architecture
                coordinate = (name, effective_architecture)
                identity = tuple(package[field] for field in IDENTITY_FIELDS)
                if coordinate in names and names[coordinate] != identity:
                    raise ValueError("APT dependency set contains two versions or origins of " + name + ": " + group + "/" + architecture)
                names[coordinate] = identity
                pending.extend(package["depends_on"])


def _validate_lock_structure(document):
    if document.get("version") != 2:
        raise ValueError("APT lock must have version 2")
    for name in ("sources", "packages", "dependency_sets"):
        if not isinstance(document.get(name), dict):
            raise ValueError("APT lock lacks " + name)
    for key, record in document["packages"].items():
        suite, name, architecture, version = package_coordinate(key)
        for field in IDENTITY_FIELDS:
            if not isinstance(record.get(field), str) or not record[field]:
                raise ValueError("package lacks " + field + ": " + key)
        if (record["suite"], record["name"], record["version"]) != (suite, name, version):
            raise ValueError("package key does not match identity: " + key)
        if record["architecture"] not in (architecture, "all"):
            raise ValueError("package architecture mismatch: " + key)
        if not isinstance(record.get("depends_on"), list):
            raise ValueError("package lacks dependency graph: " + key)
        sha = record["sha256"]
        if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("package lacks SHA256: " + key)
        filename = record["filename"]
        if filename.startswith("/") or ".." in filename.split("/"):
            raise ValueError("unsafe package filename: " + key)
        if not document["sources"].get(suite, {}).get("uris"):
            raise ValueError("package lacks source URI: " + key)
        files = record.get("files", [])
        if not isinstance(files, list) or any(not isinstance(path, str) for path in files):
            raise ValueError("invalid package file mapping: " + key)
    reachable(document)


def validate_lock(document):
    _validate_lock_structure(document)
    validate_collection_versions(document)


def reuse_locked_aliases(candidate, previous, requests):
    """Reuse one reviewed binary identity for same-content new-root aliases.

    The resolver can retain a virtual provider from one suite while selecting
    its concrete name from another. Version equality alone is insufficient:
    identical DEB bytes and a complete in-scope graph are required.
    Previously reviewed records retain priority. For the fixed Distroless
    exporter, a new duplicate may reuse its unique closed dependency graph
    only when that graph strictly contains every other same-content graph.
    Existing roots and records are never rewritten by the alias mapping.
    """
    _validate_lock_structure(candidate)
    validate_lock(previous)
    result = deepcopy(candidate)
    packages = result["packages"]
    old_packages = previous["packages"]
    aliases, additions, authoritative, existing = {}, set(), set(), set()
    closures, walks = {}, {}
    # Distroless's virtual-provider map can retain an earlier origin while
    # concrete resolution expands another same-content record (extensions.bzl
    # add_package/add_package_dependency). This is an exporter contract, not
    # an assumption that empty dependency lists mark aliases. Require complete
    # extraction identity and a unique strict graph authority, never suite order.
    content_groups = {}
    for key, record in packages.items():
        if not all(field in record for field in ("size", "section", "files")):
            continue
        identity = tuple(record[field] for field in ("name", "architecture", "version", "sha256", "size", "section")) + (tuple(record["files"]),)
        # Architecture: all bytes can still have native-architecture-specific
        # dependency edges. The lock coordinate keeps those graphs separate.
        content_groups.setdefault((package_coordinate(key)[2], identity), []).append(key)
    fresh_aliases, new_closures = {}, {}

    def new_closure(key):
        if key not in new_closures:
            seen, pending = set(), [key]
            while pending:
                item = pending.pop()
                if item not in seen:
                    if item not in packages:
                        raise ValueError("APT dependency is missing from lock: " + item)
                    seen.add(item)
                    pending.extend(packages[item]["depends_on"])
            new_closures[key] = seen
        return new_closures[key]

    for (native_architecture, identity), keys in content_groups.items():
        if len(keys) < 2 or any(package_coordinate(old_key)[2] == native_architecture and all(old[field] == identity[index] for index, field in enumerate(("name", "architecture", "version", "sha256"))) for old_key, old in old_packages.items()):
            continue
        authorities = [key for key in keys if all(
            key == other or set(packages[key]["depends_on"]) > set(packages[other]["depends_on"])
            for other in keys)]
        if len(authorities) == 1:
            authority = authorities[0]
            for key in keys:
                if key != authority:
                    fresh_aliases[key] = authority

    for key in packages.keys() & old_packages.keys():
        if any(packages[key][field] != old_packages[key][field] for field in IDENTITY_FIELDS):
            raise ValueError("locked package identity changed: " + key)

    def old_closure(key):
        if key not in closures:
            closure, pending = set(), [key]
            while pending:
                item = pending.pop()
                if item not in closure:
                    closure.add(item)
                    pending.extend(old_packages[item]["depends_on"])
            closures[key] = closure
        return closures[key]

    def same_sources(key):
        return all(result["sources"].get(old_packages[item]["suite"]) == previous["sources"].get(old_packages[item]["suite"])
                   for item in old_closure(key))

    def restore(key, allowed):
        closure = old_closure(key)
        if any(old_packages[item]["suite"] not in allowed for item in closure):
            raise ValueError("locked alias authority dependency leaves requested sources: " + key)
        authoritative.update(closure)

    def visit(key, allowed, seen):
        if key in seen:
            return aliases.get(key, key)
        seen.add(key)
        record = packages[key]
        if key in fresh_aliases:
            authority = fresh_aliases[key]
            if any(packages[item]["suite"] not in allowed for item in new_closure(key) | new_closure(authority)):
                raise ValueError("new alias authority dependency leaves requested sources: " + key)
            aliases[key] = authority
            visit(authority, allowed, seen)
            return authority
        if key in old_packages:
            existing.add(key)
        else:
            matches = [old_key for old_key, old in old_packages.items()
                       if old["suite"] in allowed
                       and package_coordinate(old_key)[2] == package_coordinate(key)[2]
                       and all(old[field] == record[field] for field in ("name", "architecture", "version", "sha256"))
                       and same_sources(old_key)]
            if matches and record["suite"] not in allowed:
                raise ValueError("new alias leaves requested sources: " + key)
            if len(matches) > 1:
                raise ValueError("ambiguous locked alias authority: " + key)
            # Different versions/digests and explicitly changed sources are
            # not aliases. The original authorization/conflict checks decide.
            if matches:
                authority = matches[0]
                restore(authority, allowed)
                if key in aliases and aliases[key] != authority:
                    raise ValueError("conflicting locked alias authorities: " + key)
                aliases[key] = authority
                return authority
            additions.add(key)
        for dependency in record["depends_on"]:
            visit(dependency, allowed, seen)
        return key

    for group, definition in result["dependency_sets"].items():
        old_sets = previous["dependency_sets"].get(group, {}).get("sets", {})
        allowed = set(requests["dependency_sets"][group].get("suites") or result["sources"])
        seen = walks.setdefault(tuple(sorted(allowed)), set())
        for architecture, roots in definition["sets"].items():
            old_roots = old_sets.get(architecture, {})
            for root, version in list(roots.items()):
                key = root + "=" + version
                replacement = visit(key, allowed, seen)
                if replacement != key and old_roots.get(root) != version:
                    del roots[root]
                    new_root, new_version = replacement.rsplit("=", 1)
                    roots[new_root] = new_version
    for key in additions:
        packages[key]["depends_on"] = [aliases.get(item, item) for item in packages[key]["depends_on"]]
    for key in existing:
        dependencies = packages[key]["depends_on"]
        mapped = [aliases.get(item, item) for item in dependencies]
        # Repeated exports may reintroduce an unknown origin under an existing
        # root. Preserve its old dictionary only after proving graph equality;
        # do not edit a reviewed edge or hide a genuine dependency change.
        if mapped != dependencies and set(mapped) == set(old_packages[key]["depends_on"]) and same_sources(key):
            authoritative.add(key)
    for key in authoritative:
        packages[key] = deepcopy(old_packages[key])
    validate_lock(result)
    return result


def merge_locks(documents):
    """Merge reviewed graphs, rejecting any inconsistent identity or digest."""
    merged = {"version": 2, "sources": {}, "dependency_sets": {}, "packages": {}, "facts": {}}
    for document in documents:
        validate_lock(document)
        for suite, record in document["sources"].items():
            old = merged["sources"].get(suite)
            if old and old != record:
                raise ValueError("conflicting source records for " + suite)
            merged["sources"][suite] = deepcopy(record)
        for group, definition in document["dependency_sets"].items():
            old = merged["dependency_sets"].get(group)
            if old and old != definition:
                raise ValueError("conflicting dependency set " + group)
            merged["dependency_sets"][group] = deepcopy(definition)
        for key, record in document["packages"].items():
            if key not in merged["packages"]:
                merged["packages"][key] = deepcopy(record)
                continue
            old = merged["packages"][key]
            for field in set(old) & set(record) - {"depends_on", "files"}:
                if old[field] != record[field]:
                    raise ValueError("conflicting package " + field + ": " + key)
            old.update({field: deepcopy(value) for field, value in record.items() if field not in old})
            old["depends_on"] = sorted(set(old["depends_on"]) | set(record["depends_on"]))
            if "files" in old or "files" in record:
                old["files"] = sorted(set(old.get("files", [])) | set(record.get("files", [])))
        for key, value in document.get("facts", {}).items():
            if key in merged["facts"] and merged["facts"][key] != value:
                raise ValueError("conflicting index identity: " + key)
            merged["facts"][key] = value
    validate_lock(merged)
    return merged


def input_record(sources, requests, resolver_version, module_requests=None):
    source_records(sources)
    validate_requests(requests)
    record = {"version": 1, "source_document": sources, "requests_document": requests,
              "resolver": {"name": "rules_distroless", "version": resolver_version}}
    if module_requests is not None:
        record["module_requests_document"] = module_requests
    record["digest"] = digest(record)
    return record


def check_lock(document, sources, requests, resolver_version=RESOLVER_VERSION):
    validate_lock(document)
    actual = document.get("_inputs", {})
    expected = input_record(sources, requests, resolver_version, actual.get("module_requests_document"))
    if actual != expected:
        raise ValueError("APT lock is missing or stale; run apt/lock.py update with the current sources and requests")
    configured = source_records(sources)
    if document["sources"] != configured:
        raise ValueError("APT lock source records do not match source configuration")
    if set(document["dependency_sets"]) != set(requests["dependency_sets"]):
        raise ValueError("APT lock dependency sets do not match requests")
    for pinned in requests.get("pinned_packages", []):
        matches = [record for record in document["packages"].values() if all(record.get(field) == pinned[field] for field in ("suite", "name", "architecture", "version"))]
        if not matches or any(record["sha256"] != pinned["sha256"] for record in matches):
            raise ValueError("fixed DEB identity or SHA256 changed: " + pinned["name"])


def finalize_result(candidate, previous, sources, requests, resolver_version,
                    allow_source_change=False, allow_version_change=False):
    """Validate a complete resolver result before replacing the production file."""
    candidate = reuse_locked_aliases(candidate, previous, requests)
    validate_lock(candidate)
    expected_sources = source_records(sources)
    if candidate["sources"] != expected_sources:
        raise ValueError("resolver output does not match selected sources")
    if set(candidate["dependency_sets"]) != set(requests["dependency_sets"]):
        raise ValueError("resolver output does not match requested package collections")
    old_coordinates = {}
    for key, record in previous.get("packages", {}).items():
        old_coordinates.setdefault(package_coordinate(key)[:3], set()).add(key)
    unchanged_sources = all(
        suite in expected_sources and all(record.get(field, []) == expected_sources[suite].get(field, [])
                                         for field in ("uris", "types", "components", "origin", "suite"))
        for suite, record in previous.get("sources", {}).items()
    )
    if previous.get("packages") and not unchanged_sources and not allow_source_change:
        raise ValueError("source change requires explicit --allow-source-change")
    result = deepcopy(candidate)
    keep = reachable(result)
    result["packages"] = {key: value for key, value in result["packages"].items() if key in keep}
    # A shared lock may retain packages owned by consumers outside the current
    # requested graph. Updating one collection must preserve those identities.
    for key, record in previous.get("packages", {}).items():
        if record["suite"] in expected_sources and key not in result["packages"]:
            result["packages"][key] = deepcopy(record)
    for key, record in result["packages"].items():
        coordinate = package_coordinate(key)[:3]
        if unchanged_sources and coordinate in old_coordinates and key not in old_coordinates[coordinate] and not allow_version_change:
            raise ValueError("existing package version changed without authorization: " + key)
        if key in previous.get("packages", {}):
            old = previous["packages"][key]
            for field in IDENTITY_FIELDS:
                if old[field] != record[field]:
                    raise ValueError("locked package identity changed: " + key + " (" + field + ")")
            for field, value in old.items():
                if field.endswith("_sha256") and field in record and record[field] != value:
                    raise ValueError("locked extracted archive hash changed: " + key + " (" + field + ")")
                if field not in record and field not in ("depends_on", "files"):
                    record[field] = deepcopy(value)
        dependencies = sorted(set(record["depends_on"]))
        old = previous.get("packages", {}).get(key)
        # Dependency closure is a set, but preserve an unchanged reviewed
        # record byte-for-byte. Canonicalize new/changed closures immediately,
        # so their first update is as stable as subsequent updates.
        record["depends_on"] = deepcopy(old["depends_on"]) if old and set(old["depends_on"]) == set(dependencies) else dependencies
        if "files" in record:
            record["files"] = sorted(set(record["files"]))
    if result.pop("_source_document", sources) != sources:
        raise ValueError("resolver source document does not match selected source configuration")
    module_requests = result.pop("_module_requests", None)
    result["_inputs"] = input_record(sources, requests, resolver_version, module_requests)
    result["_generated"] = {"notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.", "generator": "sonic-build-infra/apt/lock.py"}
    check_lock(result, sources, requests, resolver_version)
    return result


def atomic_write(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(document, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def resolution_partitions(sources, requests):
    """Keep independent suite families out of the resolver's unqualified index.

    Distroless indexes name/version/architecture, without the distribution.
    Suites requested together share one resolver; disjoint requests do not.
    The final lock retains the suite-qualified identities from every resolver.
    """
    configured = set(source_records(sources))
    validate_requests(requests)
    groups = []
    for name, request in requests["dependency_sets"].items():
        selected = set(request.get("suites") or configured)
        if not selected <= configured:
            raise ValueError("unknown source suites in " + name + ": " + ", ".join(sorted(selected - configured)))
        combined = selected.copy()
        remaining = []
        for group in groups:
            if group & selected:
                combined.update(group)
            else:
                remaining.append(group)
        groups = remaining + [combined]
    result = []
    for suites in sorted(groups, key=lambda group: sorted(group)):
        partition_sources = dict(sources, sources=[
            dict(record, suites=[suite for suite in record["suites"] if record.get("suite_keys", {}).get(suite, suite) in suites],
                 **({"suite_keys": {suite: key for suite, key in record["suite_keys"].items() if key in suites}} if "suite_keys" in record else {}))
            for record in sources["sources"] if suites.intersection([record.get("suite_keys", {}).get(suite, suite) for suite in record["suites"]])
        ])
        partition_requests = dict(requests, dependency_sets={
            name: deepcopy(request) for name, request in requests["dependency_sets"].items()
            if set(request.get("suites") or configured) <= suites
        })
        if "pinned_packages" in requests:
            partition_requests["pinned_packages"] = [
                deepcopy(package) for package in requests["pinned_packages"] if package["suite"] in suites
            ]
        result.append((suites, partition_sources, partition_requests))
    return result


def resolve(sources, requests, previous, args):
    """Resolve isolated distribution graphs and publish one reviewed lock."""
    partitions = resolution_partitions(sources, requests)
    if len(partitions) == 1 and partitions[0][1:] == (sources, requests):
        return resolve_partition(sources, requests, previous, args)
    workspace = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="sonic-apt-resolve-"))
    documents = []
    module_requests = {"version": 1, "dependency_sets": {}}
    for index, (suites, partition_sources, partition_requests) in enumerate(partitions):
        partition_previous = {
            "version": 2,
            "sources": {suite: deepcopy(record) for suite, record in previous["sources"].items() if suite in suites},
            "packages": {key: deepcopy(record) for key, record in previous["packages"].items() if record["suite"] in suites},
            "dependency_sets": {name: deepcopy(record) for name, record in previous["dependency_sets"].items()
                                if name in partition_requests["dependency_sets"]},
            "facts": deepcopy(previous.get("facts", {})),
        }
        options = argparse.Namespace(**vars(args))
        options.work_dir = workspace / ("partition-" + str(index))
        document = resolve_partition(partition_sources, partition_requests, partition_previous, options)
        if document.get("_source_document") != partition_sources:
            raise ValueError("resolver source document does not match selected partition")
        if document["sources"] != source_records(partition_sources):
            raise ValueError("resolver source records do not match selected partition")
        recorded = document.get("_module_requests", {}).get("dependency_sets", {})
        if set(recorded) != set(partition_requests["dependency_sets"]):
            raise ValueError("resolver requests do not match selected partition")
        module_requests["dependency_sets"].update(recorded)
        documents.append(document)
    merged = merge_locks(documents)
    # Configured but unrequested suites need no index resolution. Retain their
    # source records for pre-existing packages preserved by finalize_result.
    merged["sources"] = source_records(sources)
    merged["_source_document"] = deepcopy(sources)
    merged["_module_requests"] = module_requests
    return merged


def resolve_partition(sources, requests, previous, args):
    """Run the selected upstream resolver in an isolated preparation workspace."""
    workspace = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="sonic-apt-resolve-"))
    workspace.mkdir(parents=True, exist_ok=True)
    for name, document in [("sources.json", sources), ("requests.json", requests), ("previous.lock.json", previous)]:
        (workspace / name).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    (workspace / "BUILD.bazel").write_text('exports_files(["sources.json", "requests.json", "previous.lock.json"])\n')
    (workspace / "MODULE.bazel").write_text(
        'module(name = "sonic_apt_lock_update")\n'
        f'bazel_dep(name = "rules_distroless", version = {json.dumps(args.resolver_version)})\n'
        'apt = use_extension("@rules_distroless//apt:extensions.bzl", "apt")\n'
        'apt.sources_file(file = "//:sources.json")\n'
        'apt.install_file(file = "//:requests.json")\n'
        'apt.export_lock(name = "apt_lock_export", previous_lock = "//:previous.lock.json")\n'
        'use_repo(apt, "apt_lock_export")\n'
    )
    registries = args.registry or ["https://raw.githubusercontent.com/sonic-net/sonic-bazel-registry/main", "https://bcr.bazel.build/"]
    bazel = shutil.which(args.bazel) or str(Path(args.bazel).resolve())
    startup = [bazel] + args.bazel_startup_option
    command = startup + ["build"]
    command += ["--registry=" + registry for registry in registries]
    command += args.bazel_option + ["@apt_lock_export//:lock"]
    log = workspace / "resolve.log"
    with log.open("w") as output:
        result = subprocess.run(command, cwd=workspace, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"APT resolver failed ({result.returncode}); see {log}")
    # Ask Bazel for the public target output; do not depend on canonical repo names.
    query = startup + ["cquery"]
    query += ["--registry=" + registry for registry in registries] + args.bazel_option
    query += ["@apt_lock_export//:lock", "--output=files"]
    paths = subprocess.run(query, cwd=workspace, check=True, text=True, capture_output=True).stdout.splitlines()
    if len(paths) != 1:
        raise RuntimeError("APT export must produce one lock file")
    relative = Path(paths[0])
    # Repository outputs can be source files rather than build outputs.
    if relative.is_absolute():
        exported = relative
    elif (workspace / relative).exists():
        exported = workspace / relative
    else:
        info = startup + ["info"]
        info += ["--registry=" + registry for registry in registries] + args.bazel_option
        info += ["execution_root"]
        execution_root = subprocess.run(info, cwd=workspace, check=True, text=True, capture_output=True).stdout.strip()
        exported = Path(execution_root) / relative
    return reuse_locked_aliases(read_json(exported), previous, requests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["update", "check", "merge"])
    # Under `bazel run`, default paths resolve against the invoking workspace,
    # never the runfiles copy of this script.
    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    root = Path(workspace, "apt") if workspace else Path(__file__).resolve().parent
    parser.add_argument("--sources", type=Path, default=root / "sources.json")
    parser.add_argument("--requests", type=Path, default=root / "requests.json")
    parser.add_argument("--lock", type=Path, default=root / "apt.lock.json")
    parser.add_argument("--merge-lock", type=Path, action="append", default=[])
    parser.add_argument("--resolved-lock", type=Path, help="Reviewed output of the selected Distroless export; skips invocation only")
    parser.add_argument("--resolver-version", default=RESOLVER_VERSION)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--registry", action="append", default=[])
    parser.add_argument("--bazel-startup-option", action="append", default=[])
    parser.add_argument("--bazel-option", action="append", default=[])
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--allow-source-change", action="store_true")
    parser.add_argument("--allow-version-change", action="store_true")
    args = parser.parse_args()
    try:
        sources = read_json(args.sources)
        requests = read_json(args.requests)
        source_records(sources)
        validate_requests(requests)
        previous = read_json(args.lock) if args.lock.exists() else {"version": 2, "sources": {}, "packages": {}, "dependency_sets": {}, "facts": {}}
        if args.operation == "check":
            check_lock(previous, sources, requests, args.resolver_version)
            print("APT lock matches current sources, requests and package graph")
            return
        imported = [previous] if previous.get("packages") else []
        imported += [read_json(path) for path in args.merge_lock]
        if imported:
            previous = merge_locks(imported)
        if args.operation == "merge":
            atomic_write(args.lock, previous)
            print(f"Merged {len(previous['packages'])} reviewed packages; run update to resolve all requests")
            return
        candidate = read_json(args.resolved_lock) if args.resolved_lock else resolve(sources, requests, previous, args)
        final = finalize_result(candidate, previous, sources, requests, args.resolver_version,
                                args.allow_source_change, args.allow_version_change)
        atomic_write(args.lock, final)
        print(f"Locked {len(final['packages'])} packages in {len(final['dependency_sets'])} collections")
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
