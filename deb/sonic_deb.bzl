"""Unified Debian packaging from source installation TARs and final controls."""

load("@bazel_lib//lib:utils.bzl", "propagate_common_rule_attributes")
load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:defs.bzl", "cc_common")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain", "use_cc_toolchain")

_TAR_TOOLCHAIN = "@tar.bzl//tar/toolchain:type"
_DEB_ARCH = select({
    Label("@platforms//cpu:x86_64"): "amd64",
    Label("@platforms//cpu:aarch64"): "arm64",
    Label("@platforms//cpu:armv7"): "armhf",
}, no_match_error = "sonic_deb: unsupported target CPU")

def _assemble(ctx):
    cc_toolchain = find_cc_toolchain(ctx)
    features = cc_common.configure_features(ctx = ctx, cc_toolchain = cc_toolchain, requested_features = ctx.features, unsupported_features = ctx.disabled_features)
    ar = cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.cpp_link_static_library)
    tar = ctx.toolchains[_TAR_TOOLCHAIN]
    tool = ctx.attr._assembler[DefaultInfo].files_to_run
    args = ctx.actions.args()
    args.add("--data", ctx.file.data)
    args.add("--target-architecture", ctx.attr.target_architecture)
    args.add("--tar", tar.tarinfo.binary)
    args.add("--ar", ar)
    inputs = [ctx.file.data]
    if ctx.file.control:
        output = ctx.actions.declare_directory(ctx.label.name + ".debs")
        inputs.append(ctx.file.control)
        args.add("--control", ctx.file.control)
        args.add("--output-dir", output.path)
    else:
        output = ctx.actions.declare_file("{}_{}_{}.deb".format(ctx.attr.package, ctx.attr.version, ctx.attr.architecture))
        args.add("--output", output)
        for key, value in {"Package": ctx.attr.package, "Version": ctx.attr.version, "Architecture": ctx.attr.architecture, "Maintainer": ctx.attr.maintainer, "Description": ctx.attr.description, "Depends": ", ".join(ctx.attr.depends)}.items():
            if value:
                args.add("--field", key + "=" + value)
    for name, target in ctx.attr.control_files.items():
        files = target[DefaultInfo].files.to_list()
        if len(files) != 1:
            fail("control_files values must supply one file: " + name)
        inputs.append(files[0])
        args.add("--control-file", name + "=" + files[0].path)
    ctx.actions.run(
        executable = tool,
        tools = [tool] + tar.default.files.to_list(),
        inputs = depset(inputs, transitive = [cc_toolchain.all_files]),
        outputs = [output],
        arguments = [args],
        env = {"LC_ALL": "C", "LANG": "C", "PYTHONHASHSEED": "0"},
        mnemonic = "DebAssemble",
        progress_message = "Assembling Debian package %{label}",
    )
    return [DefaultInfo(files = depset([output]))]

_assemble_deb = rule(
    implementation = _assemble,
    attrs = {
        "data": attr.label(allow_single_file = True, mandatory = True),
        "control": attr.label(allow_single_file = True),
        "control_files": attr.string_keyed_label_dict(allow_files = True),
        "package": attr.string(),
        "version": attr.string(),
        "maintainer": attr.string(),
        "description": attr.string(),
        "depends": attr.string_list(),
        "architecture": attr.string(),
        "target_architecture": attr.string(mandatory = True),
        "_assembler": attr.label(default = Label("//deb:assemble"), executable = True, cfg = "exec"),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain() + [_TAR_TOOLCHAIN],
)

def _sonic_deb_impl(name, visibility, data, package, version, maintainer, description, depends, control, control_files, architecture, **kwargs):
    if control and (package or version or maintainer or description or depends or architecture != "target"):
        fail("sonic_deb: control and scalar identity/relationship fields are mutually exclusive")
    if not control and (not package or not version):
        fail("sonic_deb: scalar mode requires package and version")
    _assemble_deb(
        name = name,
        data = data,
        control = control,
        control_files = control_files,
        package = package,
        version = version,
        maintainer = maintainer,
        description = description,
        depends = depends,
        architecture = _DEB_ARCH if architecture == "target" else architecture,
        target_architecture = _DEB_ARCH,
        visibility = visibility,
        **propagate_common_rule_attributes(kwargs)
    )

sonic_deb = macro(
    implementation = _sonic_deb_impl,
    inherit_attrs = "common",
    attrs = {
        "data": attr.label(allow_files = True, mandatory = True, configurable = False),
        "control": attr.label(allow_files = True, configurable = False),
        "control_files": attr.string_keyed_label_dict(allow_files = True, configurable = False),
        "package": attr.string(configurable = False),
        "version": attr.string(configurable = False),
        "maintainer": attr.string(configurable = False),
        "description": attr.string(configurable = False),
        "depends": attr.string_list(configurable = False),
        "architecture": attr.string(default = "target", values = ["target", "all"], configurable = False),
    },
)
