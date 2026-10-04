"""Rule to wrap tar(), which ensures binaries are stripped, and creates tars with debug symbols"""

load("@bazel_lib//lib:utils.bzl", "propagate_common_rule_attributes")
load("@tar.bzl//tar:tar.bzl", "tar", "tar_rule")
load("//binary:debug_symbols.bzl", "DebugSymbolsInfo")
load("//binary:strip_binary.bzl", "strip_binary")
load(":debug_symbols_tar.bzl", "debug_symbols_tar")

def _runtime_tar_impl(ctx):
    tar_info = ctx.attr.tar[DefaultInfo]
    return [
        DefaultInfo(
            files = tar_info.files,
            runfiles = tar_info.default_runfiles,
        ),
        DebugSymbolsInfo(symbols = depset(
            transitive = [b[DebugSymbolsInfo].symbols for b in ctx.attr.binaries],
        )),
    ]

_runtime_tar = rule(
    implementation = _runtime_tar_impl,
    doc = "Re-exports a tar target's DefaultInfo and the collected DebugSymbolsInfos of its deps, for convenience. Without this, we'd have to reach into the attributes of `tar`.",
    attrs = {
        "tar": attr.label(
            mandatory = True,
            doc = "The underlying tar, forwarded unchanged",
        ),
        "binaries": attr.label_list(
            providers = [DebugSymbolsInfo],
            doc = "strip_binary targets packaged in `tar`. Their DebugSymbolsInfo is accumulated.",
        ),
    },
)

def _sonic_deploy_tar_impl(name, force_debug_build, binaries = {}, srcs = [], mtree = [], **kwargs):
    # We strip each binary and generate its mtree line pointing at the stripped ELF
    stripped_targets = []
    debug_targets = []
    debug_symbol_targets = []
    binary_mtree = []
    for mtree_prefix, binary in binaries.items():
        stripped_name = "{}_{}_stripped".format(name, binary.name)
        strip_binary(
            name = stripped_name,
            src = binary,
            force_debug_build = force_debug_build,
            **propagate_common_rule_attributes(kwargs)
        )
        debug_symbol_targets.append(":" + stripped_name)

        # Pick apart the stripped binary and debug symbols
        stripped_file = stripped_name + "_file"
        native.filegroup(
            name = stripped_file,
            srcs = [":" + stripped_name],
            output_group = "stripped",
        )
        stripped_targets.append(":" + stripped_file)
        binary_mtree.append("{} content=$(location :{})".format(mtree_prefix, stripped_file))

        # strip_binary already lays the debug info out as .build-id/NN/REST.debug.
        debug_file = stripped_name + "_debug"
        native.filegroup(
            name = debug_file,
            srcs = [":" + stripped_name],
            output_group = "debug",
        )
        debug_targets.append(":" + debug_file)

    # The runtime tar is an implementation detail kept private to this package;
    # `name` is the wrapper below, which forwards this tar's DefaultInfo unchanged
    # and attaches DebugSymbolsInfo.
    rttar_kwargs = dict(kwargs)
    rttar_kwargs.pop("visibility", None)
    tar(
        name = name + "_rttar",
        srcs = srcs + stripped_targets,
        mtree = mtree + binary_mtree,
        visibility = ["//visibility:private"],
        **rttar_kwargs
    )

    # Results:
    # - One tar that contains the stripped binaries and exposes `DebugSymbolsInfo`, and
    # - One tar that contains only the debug symbols, for conveninece.
    _runtime_tar(
        name = name,
        tar = ":" + name + "_rttar",
        binaries = debug_symbol_targets,
        visibility = kwargs["visibility"],
    )

    debug_symbols_tar(
        name = name + ".debug_symbols",
        srcs = debug_targets,
        visibility = kwargs["visibility"],
    )

sonic_deploy_tar = macro(
    doc = """Wrapper around tar(), which ensures binaries are stripped, and creates tars with debug symbols.

It produces two targets:
- `:<name>`: A tar containing stripped binaries, with fixed default timestamps for explicit entries and a `DebugSymbolsInfo` provider containing its binaries' debug symbols.
- `:<name>.debug_symbols`: A standalone tar containing the debug symbols from the binaries on this tar.
""",
    implementation = _sonic_deploy_tar_impl,
    inherit_attrs = tar_rule,
    attrs = {
        "srcs": attr.label_list(
            default = [],
            allow_files = True,
            # We're referencing the srcs later in the mtree,
            # we can't make them configurable anyway.
            configurable = False,
        ),
        "mtree": attr.string_list(
            default = [],
            configurable = False,
            doc = "Mtree entries; tar.bzl supplies default_mtime where neither the entry nor an inherited /set value supplies a timestamp.",
        ),
        "default_mtime": attr.string(
            default = "1672560000",
            doc = "Fallback archive timestamp, passed to tar.bzl. Explicit and inherited times are preserved; an empty string retains tar.bzl's original behavior.",
        ),
        "binaries": attr.string_keyed_label_dict(
            doc = """Maps an mtree prefix (e.g. \"./usr/bin/foo uid=0 gid=0 mode=0755 type=file\") to a binary target.
The binary will then be replaced with its stripped version behind the scenes. Missing time uses default_mtime; explicit time values are preserved.
""",
            default = {},
            configurable = False,
        ),
        "force_debug_build": attr.bool(
            doc = "Whether the binaries should be rebuilt with debug information before stripping. This will override Bazel's regular CLI compilation instructions, like `--strip=never`",
            default = False,
        ),
        # We do not allow mutate for now, as we rely on messing with the mtree.
        "mutate": None,
    },
)
