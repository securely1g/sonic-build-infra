"""Rule that splits a compiled binary into its stripped form and its debug symbols.

Behaviour mirrors Debian's dh_strip. It operates on ELFs directly via binutils objcopy.
"""

load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load("//toolchains/binutils:binutils_toolchain.bzl", "BINUTILS_TOOLCHAIN_TYPE")
load(":debug_symbols.bzl", "DebugSymbolsInfo")
load(":drop_build_rpath.bzl", "drop_build_rpath")
load(":keep_debug_info.bzl", "keep_debug_info")

def _strip_binary_rule_impl(ctx):
    binutils = ctx.toolchains[BINUTILS_TOOLCHAIN_TYPE].binutils

    src = ctx.file.src
    stripped = ctx.actions.declare_file(ctx.attr.name + ".stripped")

    # The debug output is a TreeArtifact: its single entry is named by the
    # binary's build-id, which is only knowable at action time.
    #
    # TODO(bazel-ready): In Bazel 9, we may be able to use `map_directory` to surface
    # the debug info as a file output.
    #   Ref: https://bazel.build/rules/lib/builtins/actions#map_directory
    debug = ctx.actions.declare_directory(ctx.attr.name + ".debug")

    args = ctx.actions.args()
    args.add(src)
    args.add(stripped)
    args.add(debug.path)
    args.add(binutils.objcopy.executable)
    args.add(binutils.readelf.executable)
    args.add(ctx.executable._patchelf)

    ctx.actions.run(
        executable = ctx.file._strip_tool,
        arguments = [args],
        inputs = [src, ctx.file._strip_tool],
        outputs = [stripped, debug],
        env={
            # If the base image is Make-built, it already has a /etc/ld.so.cache to look up installed binaries,
            # and rpaths could mess resolution order (since they have precedence over LD_LIBRARY_PATH).
            "STRIP_RPATH": "true" if ctx.attr._make_built_base[BuildSettingInfo].value else "false",
        },
        tools = [
            binutils.readelf,
            binutils.objcopy,
            ctx.attr._patchelf[DefaultInfo].files_to_run,
        ],
        mnemonic = "StripBinary",
        progress_message = "Stripping debug info from %{label}",
    )

    return [
        DefaultInfo(files = depset([stripped, debug])),
        OutputGroupInfo(
            stripped = depset([stripped]),
            debug = depset([debug]),
        ),
        DebugSymbolsInfo(symbols = depset([debug])),
    ]

_strip_binary_rule = rule(
    implementation = _strip_binary_rule_impl,
    doc = "Takes a single compiled ELF target and produces (a) a stripped version with debug " +
          "info removed, and (b) the extracted debug symbols as a <build-id>.debug file. ",
    attrs = {
        "src": attr.label(
            mandatory = True,
            allow_single_file = True,
            cfg = "target",
            doc = "The compiled ELF target to strip: an executable, shared library, or " +
                  "static library.",
        ),
        "_strip_tool": attr.label(
            default = "//binary:strip_binary.sh",
            allow_single_file = True,
            cfg = "exec",
            doc = "The script that drives objcopy to split the ELF.",
        ),
        "_patchelf": attr.label(
            default = "@patchelf//:patchelf",
            executable = True,
            cfg = "exec",
            doc = "Removes the rpath entries. binutils has no tool that edits .dynamic.",
        ),
        "_make_built_base": attr.label(
            default = "//config:make_built_base",
            doc = "Whether the deployed image's base resolves library paths without rpaths.",
        ),
    },
    toolchains = [BINUTILS_TOOLCHAIN_TYPE],
)

def _strip_binary_impl(name, src, force_debug_build, **kwargs):
    binary = src
    if force_debug_build:
        keep_debug_info_bin = "{}.debuggable".format(name)
        keep_debug_info(name = keep_debug_info_bin, src = src)
        binary = ":{}".format(keep_debug_info_bin)

    # These binaries are meant to be deployed, so we always force-strip the sandbox-releative RPATHs.
    no_build_rpath_bin = "{}.no_build_rpath".format(name)
    drop_build_rpath(name = no_build_rpath_bin, src = binary)
    binary = ":{}".format(no_build_rpath_bin)

    _strip_binary_rule(
        name = name,
        src = binary,
        **kwargs
    )

strip_binary = macro(
    doc = "Takes a single compiled ELF target and produces (a) a stripped version with debug " +
          "info removed, and (b) the extracted debug symbols as a <build-id>.debug file. ",
    implementation = _strip_binary_impl,
    inherit_attrs = _strip_binary_rule,
    attrs = {
        "force_debug_build": attr.bool(
            doc = "Whether the binary should be rebuilt with debug information before stripping. This will override Bazel's regular CLI compilation instructions, like `--strip=never`",
            default = False,
        ),
    },
)
