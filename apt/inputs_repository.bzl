"""Generate BUILD input labels from a checked APT lock without fetching packages."""

def _package_keys(lock, group, architecture, seed_keys = None):
    if seed_keys == None:
        roots = lock["dependency_sets"][group]["sets"][architecture]
        pending = [key + "=" + version for key, version in roots.items()]
    else:
        pending = list(seed_keys)
    seen = {}

    # Starlark has no while loop. Each pass follows one dependency level, so a
    # graph with N packages needs at most N levels plus a pass for visited cycles.
    for _ in range(len(lock["packages"]) + 1):
        if not pending:
            break
        dependencies = []
        for key in pending:
            if key in seen:
                continue
            if key not in lock["packages"]:
                fail("APT dependency is missing from the reviewed lock: " + key)
            package = lock["packages"][key]
            if type(package) != "dict" or any([type(package.get(field)) != "string" or not package[field] for field in ("name", "version", "architecture")]):
                fail("invalid locked APT package identity: " + key)
            parts = key.split("=", 1)
            coordinate = parts[0].rsplit(":", 1)
            name = coordinate[0].rsplit("/", 1)
            if (len(parts) != 2 or len(coordinate) != 2 or len(name) != 2 or
                name[1] != package["name"] or parts[1] != package["version"]):
                fail("APT package key does not match its identity: " + key)
            if (package["architecture"] not in (architecture, "all") or
                coordinate[1] not in (architecture, "all") or
                (coordinate[1] == "all" and package["architecture"] != "all")):
                fail("APT package has a foreign architecture: " + key)
            if type(package.get("depends_on")) != "list" or any([type(dependency) != "string" or not dependency for dependency in package["depends_on"]]):
                fail("APT package lacks dependency graph: " + key)
            seen[key] = True
            dependencies.extend(package["depends_on"])
        pending = dependencies
    return sorted(seen)

def _valid_package_name(value):
    alnum = "abcdefghijklmnopqrstuvwxyz0123456789"
    return type(value) == "string" and value and value[0] in alnum and all([character in alnum + "+.-" for character in value.elems()])

_DEL_CHARACTER = json.decode('"\\u007f"')

def _has_control(value):
    return any([character < " " or character == _DEL_CHARACTER for character in value.elems()])

def _unsafe_path(value, relative = False):
    normalized = value.lower().replace("%2e", ".").replace("%2f", "/").replace("%5c", "\\")
    parts = normalized.split("/")
    return "\\" in normalized or any([part in (".", "..") for part in parts]) or (relative and (normalized.startswith("/") or "" in parts))

def _package_urls(lock, package, key):
    filename = package["filename"]
    if not filename or _has_control(filename) or " " in filename or "?" in filename or "#" in filename or _unsafe_path(filename, relative = True):
        fail("invalid APT package filename: " + key)
    source = lock["sources"].get(package["suite"])
    if type(source) != "dict" or type(source.get("uris")) != "list" or not source["uris"]:
        fail("APT package lacks source URLs: " + key)
    urls = []
    for uri in source["uris"]:
        if (type(uri) != "string" or not uri or _has_control(uri) or " " in uri or "\\" in uri or "?" in uri or "#" in uri or
            not (uri.startswith("https://") or uri.startswith("http://"))):
            fail("invalid APT source URL: " + key)
        parts = uri.split("://", 1)[1].split("/", 1)
        if not parts[0] or "@" in parts[0] or (len(parts) == 2 and _unsafe_path(parts[1])):
            fail("invalid APT source URL: " + key)
        urls.append(uri.rstrip("/") + "/" + filename)
    return urls

def _manifest_package(lock, key):
    package = lock["packages"][key]
    for field in ("suite", "sha256", "filename"):
        if type(package.get(field)) != "string" or not package[field]:
            fail("APT package lacks " + field + ": " + key)
    if not _valid_package_name(package["name"]):
        fail("invalid APT package name: " + key)
    if key.split("=", 1)[0].rsplit(":", 1)[0] != "/" + package["suite"] + "/" + package["name"]:
        fail("APT package key does not match its identity: " + key)
    digest = package["sha256"]
    if len(digest) != 64 or any([character not in "0123456789abcdef" for character in digest.elems()]):
        fail("APT package has invalid SHA256: " + key)
    return {
        "name": package["name"],
        "version": package["version"],
        "architecture": package["architecture"],
        "urls": _package_urls(lock, package, key),
        "sha256": digest,
        "depends_on": list(package["depends_on"]),
    }

def _subset_inputs(lock, group, architecture, root_packages):
    if not group or not architecture or not root_packages:
        fail("APT subset selection requires dependency_set, architecture, and root_packages")
    if architecture == "all":
        fail("invalid APT architecture: " + architecture)
    if type(lock) != "dict" or lock.get("version") != 2:
        fail("APT subset selection requires a version-2 lock")
    for field in ("sources", "packages", "dependency_sets"):
        if type(lock.get(field)) != "dict":
            fail("APT lock lacks " + field)
    definition = lock["dependency_sets"].get(group)
    if type(definition) != "dict" or type(definition.get("sets")) != "dict":
        fail("APT lock lacks dependency set: " + group)
    roots = definition["sets"].get(architecture)
    if type(roots) != "dict" or not roots:
        fail("APT lock lacks architecture or package roots: " + group + "/" + architecture)
    if any([type(key) != "string" or not key or "=" in key or type(version) != "string" or not version for key, version in roots.items()]):
        fail("invalid locked APT root identity: " + group + "/" + architecture)

    # Validate every existing root and dependency before choosing a subset.
    rows = {}
    names = {}
    for key in _package_keys(lock, group, architecture):
        row = _manifest_package(lock, key)
        if row["name"] in names:
            fail("one public package target is required per name: " + row["name"])
        rows[key] = row
        names[row["name"]] = key

    if any([not _valid_package_name(name) for name in root_packages]):
        fail("root_packages must contain valid package names")
    if len({name: True for name in root_packages}) != len(root_packages):
        fail("root_packages must contain unique package names")
    for name in root_packages:
        if name not in names:
            fail("APT subset root is not in dependency set: " + name)

    selected = _package_keys(lock, group, architecture, [names[name] for name in root_packages])
    packages = {}
    manifest_packages = {}
    digests = {}
    for key in selected:
        row = rows[key]
        for dependency in row["depends_on"]:
            if dependency not in selected:
                fail("APT subset omits locked dependency: " + dependency)
        if row["sha256"] in digests:
            fail("APT selected packages contain duplicate SHA256: " + row["sha256"])
        digests[row["sha256"]] = True
        packages[key] = "@" + group + "//" + row["name"]
        manifest_packages[key] = row
    return {group: {architecture: packages}}, {
        "schema": 1,
        "dependency_set": group,
        "architecture": architecture,
        "root_packages": sorted(root_packages),
        "packages": manifest_packages,
    }

def _apt_inputs_impl(repository_ctx):
    lock = json.decode(repository_ctx.read(repository_ctx.attr.lock))
    subset = repository_ctx.attr.dependency_set or repository_ctx.attr.architecture or repository_ctx.attr.root_packages
    manifest = None
    if subset:
        inputs, manifest = _subset_inputs(lock, repository_ctx.attr.dependency_set, repository_ctx.attr.architecture, repository_ctx.attr.root_packages)
    else:
        inputs = {}
        for group in sorted(lock["dependency_sets"]):
            inputs[group] = {}
            for architecture in sorted(lock["dependency_sets"][group]["sets"]):
                packages = {}
                names = {}
                for key in _package_keys(lock, group, architecture):
                    name = lock["packages"][key]["name"]
                    if name in names:
                        fail("one public package target is required per name: " + name)
                    names[name] = True

                    # Keep strings: the consuming BUILD file supplies the repository
                    # aliases declared by use_repo(apt, ...), not this metadata repo.
                    packages[key] = "@" + group + "//" + name
                inputs[group][architecture] = packages
    header = "# AUTO-GENERATED. DO NOT EDIT MANUALLY.\n# Generator: sonic-build-infra/apt/inputs_repository.bzl\n# Bazel regenerates this file from the reviewed APT lock.\n\n"
    repository_ctx.file("apt_inputs.bzl", header + "APT_INPUTS = " + json.encode_indent(inputs, indent = "    ") + "\n")
    exports = ["apt_inputs.bzl"]
    if subset:
        repository_ctx.file("apt_manifest.json", json.encode_indent(manifest, indent = "    ") + "\n")
        exports.append("apt_manifest.json")
    repository_ctx.file("BUILD.bazel", header + "exports_files(" + json.encode(exports) + ', visibility = ["//visibility:public"])\n')

apt_inputs = repository_rule(
    implementation = _apt_inputs_impl,
    attrs = {
        "lock": attr.label(mandatory = True, allow_single_file = [".json"]),
        "dependency_set": attr.string(),
        "architecture": attr.string(),
        "root_packages": attr.string_list(),
    },
    doc = "Read the canonical APT lock and expose APT_INPUTS; optional package roots restrict one set/architecture and add apt_manifest.json. Resolve and fetch no packages.",
)
