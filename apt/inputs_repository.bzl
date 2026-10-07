"""Generate BUILD input labels from a checked APT lock without fetching packages."""

def _package_keys(lock, group, architecture):
    roots = lock["dependency_sets"][group]["sets"][architecture]
    pending = [key + "=" + version for key, version in roots.items()]
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
            coordinate, version = key.split("=", 1)
            name, key_arch = coordinate.rsplit(":", 1)
            if name.rsplit("/", 1)[1] != package["name"] or version != package["version"]:
                fail("APT package key does not match its identity: " + key)
            if (package["architecture"] not in (architecture, "all") or
                key_arch not in (architecture, "all") or
                (key_arch == "all" and package["architecture"] != "all")):
                fail("APT package has a foreign architecture: " + key)
            seen[key] = True
            dependencies.extend(package["depends_on"])
        pending = dependencies
    return sorted(seen)

def _apt_inputs_impl(repository_ctx):
    lock = json.decode(repository_ctx.read(repository_ctx.attr.lock))
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
    repository_ctx.file("BUILD.bazel", header + 'exports_files(["apt_inputs.bzl"], visibility = ["//visibility:public"])\n')

apt_inputs = repository_rule(
    implementation = _apt_inputs_impl,
    attrs = {
        "lock": attr.label(mandatory = True, allow_single_file = [".json"]),
    },
    doc = "Read the canonical APT lock and expose APT_INPUTS in apt_inputs.bzl; resolve and fetch no packages.",
)
