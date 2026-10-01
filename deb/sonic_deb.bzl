"""Build a Debian `.deb` from a pre-packaged data tar.

Please note that, for simplicity, we don't support the whole interface of a deb package.
Feel free to add methods and attributes (e.g. 'Homepage') as needed.
"""

load("@bazel_lib//lib:utils.bzl", "propagate_common_rule_attributes")

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

def _sonic_deb_build_impl(ctx):
    # The architecture is selected from the target platform, while dpkg-deb and
    # the staging runner run on the execution platform.
    deb_name = "{}_{}_{}.deb".format(ctx.attr.package, ctx.attr.version, ctx.attr.architecture)
    output_deb = ctx.actions.declare_file(deb_name)
    control = ctx.actions.declare_file(ctx.label.name + ".control")
    control_lines = [
        "Package: %s" % ctx.attr.package,
        "Version: %s" % ctx.attr.version,
        "Maintainer: %s" % ctx.attr.maintainer,
    ]
    if ctx.attr.depends:
        control_lines.append("Depends: %s" % ", ".join(ctx.attr.depends))
    control_lines.append("Architecture: %s" % ctx.attr.architecture)
    control_lines.append("Description: %s" % ctx.attr.description)
    ctx.actions.write(control, "\n".join(control_lines) + "\n")

    arguments = ctx.actions.args()
    arguments.add("--data-tar", ctx.file.data_tar)
    arguments.add("--control", control)
    arguments.add("--dpkg-deb", ctx.executable._dpkg_deb)
    arguments.add("--output", output_deb)
    ctx.actions.run(
        executable = ctx.executable._build_tool,
        arguments = [arguments],
        inputs = [ctx.file.data_tar, control],
        outputs = [output_deb],
        tools = [
            ctx.attr._build_tool[DefaultInfo].files_to_run,
            ctx.attr._dpkg_deb[DefaultInfo].files_to_run,
        ],
        mnemonic = "DebBuild",
        progress_message = "Building %s with dpkg-deb" % deb_name,
    )
    return [DefaultInfo(files = depset([output_deb]))]

_sonic_deb_build = rule(
    doc = "Stage a package tree and let dpkg-deb build its archives and metadata.",
    implementation = _sonic_deb_build_impl,
    attrs = {
        "data_tar": attr.label(mandatory = True, allow_single_file = True),
        "package": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
        "architecture": attr.string(mandatory = True),
        "maintainer": attr.string(),
        "description": attr.string(),
        "depends": attr.string_list(),
        "_build_tool": attr.label(
            default = Label("//deb:build_deb"),
            executable = True,
            cfg = "exec",
        ),
        "_dpkg_deb": attr.label(
            default = Label("//deb:dpkg_deb"),
            executable = True,
            cfg = "exec",
        ),
    },
)

def _sonic_deb_impl(name, visibility, data, package, version, maintainer, description, depends, **kwargs):
    _sonic_deb_build(
        name = name,
        data_tar = data,
        package = package,
        version = version,
        architecture = _DEB_ARCH,
        maintainer = maintainer,
        description = description,
        depends = depends,
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
            doc = "A payload tar staged with standard tar extraction and packaged by dpkg-deb. File bytes, modes and links are preserved.",
        ),
        "package": attr.string(mandatory = True, configurable = False, doc = "Debian `Package:` field."),
        "version": attr.string(mandatory = True, configurable = False, doc = "Debian `Version:` field."),
        "maintainer": attr.string(default = "", configurable = False, doc = "Debian `Maintainer:` field."),
        "description": attr.string(default = "", configurable = False, doc = "Debian `Description:` field."),
        "depends": attr.string_list(configurable = False, doc = "Debian `Depends:` list (hand-written; no auto-shlibs)."),
    },
)
