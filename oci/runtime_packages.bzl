"""Derive OCI package metadata from existing Debian recipes and built TARs."""

load("@tar.bzl//tar:tar.bzl", "tar_lib")

RuntimePackagesInfo = provider(
    doc = "Generated package controls and target libraries for dependency analysis.",
    fields = {
        "metadata": "Generated owner metadata JSON.",
        "trees": "Transitive depset of staged target package trees, never execution libraries.",
    },
)

def _single(target, description):
    files = target[DefaultInfo].files.to_list()
    if len(files) != 1:
        fail(description + " must supply exactly one file")
    return files[0]

def _output(ctx):
    return ctx.actions.declare_file(ctx.label.name if ctx.label.name.endswith(".json") else ctx.label.name + ".json")

def _runtime_packages_impl(ctx):
    output = _output(ctx)
    tree = ctx.actions.declare_directory(ctx.label.name + ".packages")
    tar = ctx.toolchains[tar_lib.toolchain_type]
    args = ctx.actions.args()
    args.add("--control", ctx.file.control)
    if ctx.file.changelog:
        args.add("--changelog", ctx.file.changelog)
    args.add("--module", ctx.attr.module_name)
    args.add("--architecture", ctx.attr.architecture)
    args.add("--tools", ctx.file._tools.path)
    args.add("--tar", tar.tarinfo.binary)
    args.add("--output", output)
    args.add("--tree", tree.path)
    inputs = [ctx.file.control, ctx.file._tools] + ctx.files.changelog
    for package, target in ctx.attr.packages.items():
        payload = _single(target, "runtime TAR for " + package)
        inputs.append(payload)
        args.add("--package", package + "=" + ctx.attr.declared_targets[package] + "=" + payload.path)
    for package, paths in ctx.attr.required_paths.items():
        if package not in ctx.attr.packages or not paths:
            fail("required_paths needs a declared package and at least one path: " + package)
        for path in paths:
            args.add("--required-path", package + "=" + path)
    if len(ctx.attr.apt_data) != len(ctx.attr.apt_controls):
        fail("APT data and controls must be declared in pairs")
    for data, control in zip(ctx.attr._abi_data + ctx.attr.apt_data, ctx.attr._abi_controls + ctx.attr.apt_controls):
        payload = _single(data, "APT payload")
        fields = _single(control, "APT controls")
        inputs.extend([payload, fields])
        args.add("--apt-package", payload.path + "=" + fields.path)
    for option, values in [("--symbols", ctx.attr.symbols), ("--substvars", ctx.attr.substvars)]:
        for package, target in values.items():
            file = _single(target, "Debian " + option[2:] + " for " + package)
            inputs.append(file)
            args.add(option, package + "=" + file.path)
    for path, target in ctx.attr.debian_files.items():
        file = _single(target, "Debian helper input " + path)
        inputs.append(file)
        args.add("--debian-file", path + "=" + file.path)
    dependencies = [target[RuntimePackagesInfo].trees for target in ctx.attr.dependencies]
    trees = depset(transitive = dependencies)
    args.add_all(trees, before_each = "--dependency", expand_directories = False)
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = [args],
        inputs = depset(inputs, transitive = [trees]),
        tools = tar.default.files,
        outputs = [output, tree],
        env = {"LANG": "C", "LC_ALL": "C", "PYTHONHASHSEED": "0"},
        mnemonic = "DebianRuntimeMetadata",
        progress_message = "Derive package controls from Debian recipes and target binaries for %{label}",
    )
    return [
        DefaultInfo(files = depset([output])),
        RuntimePackagesInfo(metadata = output, trees = depset([tree], transitive = dependencies)),
        OutputGroupInfo(debian = depset([tree])),
    ]

_runtime_packages = rule(
    implementation = _runtime_packages_impl,
    attrs = {
        "control": attr.label(allow_single_file = True, mandatory = True),
        "changelog": attr.label(allow_single_file = True),
        "module_name": attr.string(mandatory = True),
        "architecture": attr.string(mandatory = True, values = ["amd64", "arm64"]),
        "packages": attr.string_keyed_label_dict(allow_files = True, mandatory = True),
        "declared_targets": attr.string_dict(mandatory = True),
        "required_paths": attr.string_list_dict(),
        "dependencies": attr.label_list(providers = [RuntimePackagesInfo]),
        "apt_data": attr.label_list(allow_files = True),
        "apt_controls": attr.label_list(allow_files = True),
        "symbols": attr.string_keyed_label_dict(allow_files = True),
        "substvars": attr.string_keyed_label_dict(allow_files = True),
        "debian_files": attr.string_keyed_label_dict(allow_files = True),
        # These are the target compiler ABI packages, not tools selected by cfg=exec.
        "_abi_data": attr.label_list(default = [Label("@sysroot//" + name + ":data") for name in ["libc6", "libgcc-s1", "libstdc++6"]]),
        "_abi_controls": attr.label_list(default = [Label("@sysroot//" + name + ":control") for name in ["libc6", "libgcc-s1", "libstdc++6"]]),
        "_tool": attr.label(default = Label("//oci:runtime_packages"), executable = True, cfg = "exec"),
        "_tools": attr.label(default = Label("//tools/build_tools:kernel_runtime"), allow_single_file = True, cfg = "exec"),
    },
    toolchains = [tar_lib.toolchain_type],
)

def runtime_packages(name, packages, control, changelog = None, apt_packages = {}, module_name = None, architecture = None, **kwargs):
    """Generate owner metadata; package facts stay in the existing Debian files.

    packages maps Debian binary package names to their public runtime TAR targets.
    required_paths optionally maps package names to reviewed runtime TAR paths.
    Each listed path must exist before metadata is published; {multiarch} expands
    for the target architecture. Images may add their own integration checks.
    dependencies supplies source-built library metadata in target configuration.
    apt_packages maps target APT data labels to their matching control labels.
    Neither input selects libraries from the execution tools' private runtime.
    changelog may be omitted only for an existing finalized binary control file.
    """
    labels = {package: native.package_relative_label(target) for package, target in packages.items()}
    _runtime_packages(
        name = name,
        packages = packages,
        control = control,
        changelog = changelog,
        module_name = module_name or native.module_name(),
        architecture = architecture or select({
            Label("@platforms//cpu:x86_64"): "amd64",
            Label("@platforms//cpu:aarch64"): "arm64",
        }),
        declared_targets = {package: "//" + target.package + ":" + target.name for package, target in labels.items()},
        apt_data = apt_packages.keys(),
        apt_controls = apt_packages.values(),
        **kwargs
    )

def _group_impl(ctx):
    output = _output(ctx)
    args = ctx.actions.args()
    args.add("--output", output)
    files = [target[RuntimePackagesInfo].metadata for target in ctx.attr.packages]
    args.add_all(files, before_each = "--merge")
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = [args],
        inputs = files,
        outputs = [output],
        mnemonic = "MergeRuntimeMetadata",
    )
    return [
        DefaultInfo(files = depset([output])),
        RuntimePackagesInfo(
            metadata = output,
            trees = depset(transitive = [target[RuntimePackagesInfo].trees for target in ctx.attr.packages]),
        ),
    ]

runtime_packages_group = rule(
    implementation = _group_impl,
    doc = "Publish packages with separate existing Debian recipes as one owner interface.",
    attrs = {
        "packages": attr.label_list(mandatory = True, providers = [RuntimePackagesInfo]),
        "_tool": attr.label(default = Label("//oci:runtime_packages"), executable = True, cfg = "exec"),
    },
)
