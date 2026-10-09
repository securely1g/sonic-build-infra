"""Export reviewed package identities as ordinary Distroless declarations."""

import json


def package_keys(lock, dependency_set, architecture):
    roots = lock["dependency_sets"][dependency_set]["sets"][architecture]
    pending = [key + "=" + version for key, version in roots.items()]
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        package = lock["packages"][key]
        coordinate, version = key.split("=", 1)
        package_name, key_arch = coordinate.rsplit(":", 1)
        if package_name.rsplit("/", 1)[1] != package["name"] or version != package["version"]:
            raise ValueError("APT package key does not match its identity: " + key)
        if (package["architecture"] not in (architecture, "all") or key_arch not in (architecture, "all") or
                (key_arch == "all" and package["architecture"] != "all")):
            raise ValueError("APT package has a foreign architecture: " + key)
        seen.add(key)
        pending.extend(package["depends_on"])
    return sorted(seen)


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
