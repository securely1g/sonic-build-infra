"""Export reviewed package identities as ordinary Distroless declarations."""

import json


def package_keys(lock, dependency_set, architecture, seed_keys=None):
    if seed_keys is None:
        roots = lock["dependency_sets"][dependency_set]["sets"][architecture]
        pending = [key + "=" + version for key, version in roots.items()]
    else:
        pending = list(seed_keys)
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        if key not in lock["packages"]:
            raise ValueError("APT dependency is missing from the reviewed lock: " + key)
        package = lock["packages"][key]
        if not isinstance(package, dict) or any(not isinstance(package.get(field), str) or not package[field]
                                                for field in ("name", "version", "architecture")):
            raise ValueError("invalid locked APT package identity: " + key)
        parts = key.split("=", 1)
        coordinate = parts[0].rsplit(":", 1)
        name = coordinate[0].rsplit("/", 1)
        if (len(parts) != 2 or len(coordinate) != 2 or len(name) != 2 or
                name[1] != package["name"] or parts[1] != package["version"]):
            raise ValueError("APT package key does not match its identity: " + key)
        if (package["architecture"] not in (architecture, "all") or coordinate[1] not in (architecture, "all") or
                (coordinate[1] == "all" and package["architecture"] != "all")):
            raise ValueError("APT package has a foreign architecture: " + key)
        if not isinstance(package.get("depends_on"), list) or any(not isinstance(dependency, str) or not dependency
                                                                  for dependency in package["depends_on"]):
            raise ValueError("APT package lacks dependency graph: " + key)
        seen.add(key)
        pending.extend(package["depends_on"])
    return sorted(seen)


def _valid_package_name(value):
    alnum = "abcdefghijklmnopqrstuvwxyz0123456789"
    return isinstance(value, str) and bool(value) and value[0] in alnum and all(char in alnum + "+.-" for char in value)


def _has_control(value):
    return any(char < " " or char == "\x7f" for char in value)


def _unsafe_path(value, relative=False):
    normalized = value.lower().replace("%2e", ".").replace("%2f", "/").replace("%5c", "\\")
    parts = normalized.split("/")
    return "\\" in normalized or any(part in (".", "..") for part in parts) or (relative and (normalized.startswith("/") or "" in parts))


def _package_urls(lock, package, key):
    filename = package["filename"]
    if not filename or _has_control(filename) or " " in filename or "?" in filename or "#" in filename or _unsafe_path(filename, relative=True):
        raise ValueError("invalid APT package filename: " + key)
    source = lock["sources"].get(package["suite"])
    if not isinstance(source, dict) or not isinstance(source.get("uris"), list) or not source["uris"]:
        raise ValueError("APT package lacks source URLs: " + key)
    urls = []
    for uri in source["uris"]:
        if (not isinstance(uri, str) or not uri or _has_control(uri) or " " in uri or "\\" in uri or "?" in uri or "#" in uri or
                not uri.startswith(("https://", "http://"))):
            raise ValueError("invalid APT source URL: " + key)
        parts = uri.split("://", 1)[1].split("/", 1)
        if not parts[0] or "@" in parts[0] or (len(parts) == 2 and _unsafe_path(parts[1])):
            raise ValueError("invalid APT source URL: " + key)
        urls.append(uri.rstrip("/") + "/" + filename)
    return urls


def _manifest_package(lock, key):
    package = lock["packages"][key]
    for field in ("suite", "sha256", "filename"):
        if not isinstance(package.get(field), str) or not package[field]:
            raise ValueError("APT package lacks " + field + ": " + key)
    if not _valid_package_name(package["name"]):
        raise ValueError("invalid APT package name: " + key)
    if key.split("=", 1)[0].rsplit(":", 1)[0] != "/" + package["suite"] + "/" + package["name"]:
        raise ValueError("APT package key does not match its identity: " + key)
    digest = package["sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("APT package has invalid SHA256: " + key)
    return {
        "name": package["name"],
        "version": package["version"],
        "architecture": package["architecture"],
        "urls": _package_urls(lock, package, key),
        "sha256": digest,
        "depends_on": list(package["depends_on"]),
    }


def subset_inputs(lock, dependency_set, architecture, root_packages):
    """Return labels and metadata for named seeds in one complete checked closure."""
    if not dependency_set or not architecture or not root_packages or not isinstance(root_packages, list):
        raise ValueError("APT subset selection requires dependency_set, architecture, and root_packages")
    if architecture == "all":
        raise ValueError("invalid APT architecture: " + str(architecture))
    if not isinstance(lock, dict) or lock.get("version") != 2:
        raise ValueError("APT subset selection requires a version-2 lock")
    for field in ("sources", "packages", "dependency_sets"):
        if not isinstance(lock.get(field), dict):
            raise ValueError("APT lock lacks " + field)
    definition = lock["dependency_sets"].get(dependency_set)
    if not isinstance(definition, dict) or not isinstance(definition.get("sets"), dict):
        raise ValueError("APT lock lacks dependency set: " + dependency_set)
    roots = definition["sets"].get(architecture)
    if not isinstance(roots, dict) or not roots:
        raise ValueError("APT lock lacks architecture or package roots: " + dependency_set + "/" + architecture)
    if any(not isinstance(key, str) or not key or "=" in key or not isinstance(version, str) or not version
           for key, version in roots.items()):
        raise ValueError("invalid locked APT root identity: " + dependency_set + "/" + architecture)

    # Validate the whole existing group/architecture before mapping subset names.
    rows, names = {}, {}
    for key in package_keys(lock, dependency_set, architecture):
        row = _manifest_package(lock, key)
        if row["name"] in names:
            raise ValueError("one public package target is required per name: " + row["name"])
        rows[key] = row
        names[row["name"]] = key

    if any(not _valid_package_name(name) for name in root_packages):
        raise ValueError("root_packages must contain valid package names")
    if len(set(root_packages)) != len(root_packages):
        raise ValueError("root_packages must contain unique package names")
    for name in root_packages:
        if name not in names:
            raise ValueError("APT subset root is not in dependency set: " + name)

    selected = package_keys(lock, dependency_set, architecture, [names[name] for name in root_packages])
    packages, manifest_packages, digests = {}, {}, set()
    for key in selected:
        row = rows[key]
        for dependency in row["depends_on"]:
            if dependency not in selected:
                raise ValueError("APT subset omits locked dependency: " + dependency)
        if row["sha256"] in digests:
            raise ValueError("APT selected packages contain duplicate SHA256: " + row["sha256"])
        digests.add(row["sha256"])
        packages[key] = "@" + dependency_set + "//" + row["name"]
        manifest_packages[key] = row
    return {dependency_set: {architecture: packages}}, {
        "schema": 1,
        "dependency_set": dependency_set,
        "architecture": architecture,
        "root_packages": sorted(root_packages),
        "packages": manifest_packages,
    }


def declarations(lock):
    """Return MODULE text and reference BUILD labels for repository-rule checks."""
    header = "# AUTO-GENERATED. DO NOT EDIT MANUALLY.\n# Generator: sonic-build-infra/apt/export_inputs.py\n# Regenerate from the reviewed apt.lock.json.\n\n"
    lines = [header, 'apt = use_extension("@rules_distroless//apt:extensions.bzl", "apt")\n']
    inputs = {}
    for group, value in sorted(lock["dependency_sets"].items()):
        constraints, inputs[group] = [], {}
        for arch in sorted(value["sets"]):
            packages, names = {}, set()
            for key in package_keys(lock, group, arch):
                package = lock["packages"][key]
                name = package["name"]
                if name in names:
                    raise ValueError("one public package target is required per name: " + name)
                names.add(name)
                constraints.append(name + " (= " + package["version"] + ") [" + arch + "]")
                packages[key] = "@" + group + "//" + name
            inputs[group][arch] = packages
        lines += ["\napt.install(\n", "    dependency_set = " + json.dumps(group) + ",\n", "    packages = [\n"]
        lines += ["        " + json.dumps(item) + ",\n" for item in constraints]
        lines += ["    ],\n", "    suites = " + json.dumps(sorted(lock["sources"])) + ",\n", ")\n"]
    lines += ["\nuse_repo(apt, " + ", ".join(json.dumps(group) for group in sorted(inputs)) + ")\n"]
    return "".join(lines), header + "APT_INPUTS = " + json.dumps(inputs, indent=4, sort_keys=True) + "\n"
