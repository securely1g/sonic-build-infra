"""Build a Debian `.deb` from a pre-packaged data tar.

Please note that, for simplicity, we don't support the whole interface of a deb package.
Feel free to add methods and attributes (e.g. 'Homepage') as needed.
"""

load("@bazel_lib//lib:utils.bzl", "propagate_common_rule_attributes")
load("@bazel_skylib//rules:write_file.bzl", "write_file")
load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:defs.bzl", "cc_common")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain", "use_cc_toolchain")
load("@tar.bzl", "tar")

_TAR_TOOLCHAIN = "@tar.bzl//tar/toolchain:type"

# Map each Bazel CPU constraint to its Debian arch name
# so the architecture is derived from the target platform:
#   `bazel build --platforms=//...:arm64` yields an arm64 deb.
# Unlisted CPUs fail fast at analysis.
_CPU_TO_DEB_ARCH = {
    Label("@platforms//cpu:x86_64"): "amd64",
    Label("@platforms//cpu:aarch64"): "arm64",
    Label("@platforms//cpu:armv7"): "armhf",
}

_UNSUPPORTED_CPU_ERROR = "sonic_deb: unsupported target CPU. Supported: amd64/arm64/armhf."

_DEB_ARCH = select(_CPU_TO_DEB_ARCH, no_match_error = _UNSUPPORTED_CPU_ERROR)

_DEB_ARCH_LINE = select(
    {cpu: ["Architecture: %s" % arch] for cpu, arch in _CPU_TO_DEB_ARCH.items()},
    no_match_error = _UNSUPPORTED_CPU_ERROR,
)

def _sonic_md5sums_from_tar_impl(ctx):
    """Generate the dpkg `md5sums` control file by streaming the data tar."""
    out = ctx.actions.declare_file(ctx.label.name + ".md5sums")
    tool = ctx.executable._md5_tool
    ctx.actions.run(
        executable = tool,
        arguments = [ctx.file.data_tar.path, out.path],
        inputs = [ctx.file.data_tar],
        outputs = [out],
        # py_binary output is a wrapper script; its runfiles carry the
        # interpreter + md5sums_from_tar.py, so they must travel with it.
        tools = depset(
            direct = [tool],
            transitive = [ctx.attr._md5_tool[DefaultInfo].default_runfiles.files],
        ),
        mnemonic = "DebMd5sums",
        progress_message = "Computing md5sums for %s" % ctx.label.name,
    )
    return [DefaultInfo(files = depset([out]))]

_sonic_md5sums_from_tar = rule(
    doc = "Given a tar, create a compatible .md5sums file for its contents",
    implementation = _sonic_md5sums_from_tar_impl,
    attrs = {
        "data_tar": attr.label(allow_single_file = True, mandatory = True),
        "_md5_tool": attr.label(
            default = Label("//deb:md5sums_from_tar"),
            executable = True,
            cfg = "exec",
        ),
    },
)

def _sonic_control_tar_impl(name, visibility, package, version, maintainer, description, depends, md5sums_file):
    # dpkg requires Package, Version, Architecture, Maintainer, Description.
    # Depends is the only relationship field we carry (SONiC hand-writes it).
    control_lines = [
        "Package: %s" % package,
        "Version: %s" % version,
        "Maintainer: %s" % maintainer,
    ]
    if depends:
        control_lines.append("Depends: %s" % ", ".join(depends))

    # Architecture is derived at analysis time from --platform,
    # which menas it's a select() (and not a string),
    # which means we can only concatenate it into the list.
    content = control_lines + _DEB_ARCH_LINE + [
        # Description comes last, as it may be a multiline string.
        "Description: %s" % description,
        # Extra necessary empty line, because write_line doesn't add it.
        "",
    ]

    write_file(
        name = name + "_file",
        out = name + ".control",
        content = content,
    )

    tar(
        name = name,
        srcs = [name + "_file", md5sums_file],
        # We always need a compressed control file.
        compress = "gzip",
        mtree = [
            ". uid=0 gid=0 time=0 mode=0755 type=dir",
            "./control uid=0 gid=0 time=0 mode=0644 type=file content=$(location %s_file)" % name,
            "./md5sums uid=0 gid=0 time=0 mode=0644 type=file content=$(location %s)" % md5sums_file,
        ],
        visibility = visibility,
    )

_sonic_control_tar = macro(
    doc = "Build control.tar.gz (the `control` file and `md5sums`)",
    implementation = _sonic_control_tar_impl,
    attrs = {
        "package": attr.string(mandatory = True, configurable = False),
        "version": attr.string(mandatory = True, configurable = False),
        "maintainer": attr.string(default = "", configurable = False),
        "description": attr.string(default = "", configurable = False),
        "depends": attr.string_list(configurable = False),
        "md5sums_file": attr.label(mandatory = True, allow_files = True, configurable = False),
    },
)

def _sonic_deb_assemble_impl(ctx):
    """Assemble the .deb (ar of debian-binary + control.tar.gz + data.tar.gz)."""

    # Debian filename convention: <package>_<version>_<arch>.deb. We're in a rule
    # impl (analysis time), so ctx.attr.architecture is a concrete string even
    # though the caller passed a platform-derived select().
    deb_name = "{}_{}_{}.deb".format(ctx.attr.package, ctx.attr.version, ctx.attr.architecture)
    output_deb = ctx.actions.declare_file(deb_name)

    # `ar` comes from the CC toolchain.
    cc_toolchain = find_cc_toolchain(ctx)
    feature_configuration = cc_common.configure_features(
        ctx = ctx,
        cc_toolchain = cc_toolchain,
        requested_features = ctx.features,
        unsupported_features = ctx.disabled_features,
    )
    ar_path = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_link_static_library,
    )

    bsdtar = ctx.toolchains[_TAR_TOOLCHAIN]
    tar_path = bsdtar.tarinfo.binary.path

    # deb members must be in this exact order: debian-binary, control, data.
    script = """#!/bin/bash
set -e
WORKDIR=$(mktemp -d)
trap "rm -rf $WORKDIR" EXIT

echo "2.0" > "$WORKDIR/debian-binary"
cp "{control_tar}" "$WORKDIR/control.tar.gz"
# data.tar may come uncompressed from upstream, so we need to make sure it's gzipped
"{tar}" -czf "$WORKDIR/data.tar.gz" --uid=0 --gid=0 --numeric-owner @"{data_tar}"

"{ar}" rc "{deb}" "$WORKDIR/debian-binary" "$WORKDIR/control.tar.gz" "$WORKDIR/data.tar.gz"
""".format(
        control_tar = ctx.file.control_tar.path,
        tar = tar_path,
        data_tar = ctx.file.data_tar.path,
        ar = ar_path,
        deb = output_deb.path,
    )
    ctx.actions.run_shell(
        outputs = [output_deb],
        inputs = depset(
            [ctx.file.control_tar, ctx.file.data_tar],
            transitive = [cc_toolchain.all_files],
        ),
        tools = bsdtar.default.files,
        command = script,
        mnemonic = "DebAssemble",
        progress_message = "Assembling %s" % deb_name,
    )
    return [DefaultInfo(files = depset([output_deb]))]

_sonic_deb_assemble = rule(
    doc = "Assemble the final deb package. Ref: https://man7.org/linux/man-pages/man5/deb.5.html",
    implementation = _sonic_deb_assemble_impl,
    attrs = {
        "data_tar": attr.label(mandatory = True, allow_single_file = True),
        "control_tar": attr.label(mandatory = True, allow_single_file = True),
        "package": attr.string(mandatory = True, doc = "Debian `Package:`; first field of the .deb filename."),
        "version": attr.string(mandatory = True, doc = "Debian `Version:`; second field of the .deb filename."),
        "architecture": attr.string(mandatory = True, doc = "Debian arch; third field of the .deb filename. Pass the platform-derived _DEB_ARCH select()."),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain() + [_TAR_TOOLCHAIN],
)

def _sonic_deb_impl(name, visibility, data, package, version, maintainer, description, depends, **kwargs):
    md5sums = name + "_md5sums"
    control = name + "_control"

    _sonic_md5sums_from_tar(
        name = md5sums,
        data_tar = data,
    )
    _sonic_control_tar(
        name = control,
        package = package,
        version = version,
        maintainer = maintainer,
        description = description,
        depends = depends,
        md5sums_file = ":" + md5sums,
    )

    _sonic_deb_assemble(
        name = name,
        data_tar = data,
        control_tar = ":" + control,
        package = package,
        version = version,
        architecture = _DEB_ARCH,
        visibility = visibility,
        **propagate_common_rule_attributes(kwargs)
    )

sonic_deb = macro(
    doc = """Build a `.deb`.

    The output file is named `<package>_<version>_<arch>.deb`,
    with `arch` derived from the target platform (i.e. `--platform` flag).

    Only the dpkg-required control fields plus `Depends` are emitted.
    Add further fields / maintainer scripts (e.g. `Homepage`) here when a migrated package needs them.
    """,
    implementation = _sonic_deb_impl,
    # So callers can set `tags` (and the other common attributes) on a deb.
    inherit_attrs = "common",
    attrs = {
        "data": attr.label(
            mandatory = True,
            allow_files = True,
            configurable = False,
            doc = "A tar holding the actual contents of the deb archive. Will be included verbatim.",
        ),
        "package": attr.string(mandatory = True, configurable = False, doc = "Debian `Package:` field."),
        "version": attr.string(mandatory = True, configurable = False, doc = "Debian `Version:` field."),
        "maintainer": attr.string(default = "", configurable = False, doc = "Debian `Maintainer:` field."),
        "description": attr.string(default = "", configurable = False, doc = "Debian `Description:` field."),
        "depends": attr.string_list(configurable = False, doc = "Debian `Depends:` list (hand-written; no auto-shlibs)."),
    },
)
