"""Toolchain exposing useful binutils binaries, such as readelf, that are not exposed by the CC toolchain."""

BINUTILS_TOOLCHAIN_TYPE = Label("//toolchains/binutils:toolchain_type")

BINUTILS_TOOLS = [
    "readelf",
    "objcopy",
    "objdump",
]

BinutilsInfo = provider(
    doc = "Paths to different binutils, such as readelf, for the execution platform. This exists because cc toolchains do not forward certain binutils tools.",
    fields = {
        tool: "FilesToRun provider to the {} binary".format(tool)
        for tool in BINUTILS_TOOLS
    },
)

def _binutils_toolchain_impl(ctx):
    toolchain_info = platform_common.ToolchainInfo(
        binutils = BinutilsInfo(**{
            tool: getattr(ctx.attr, tool)[DefaultInfo].files_to_run
            for tool in BINUTILS_TOOLS
        }),
    )
    return [
        toolchain_info,
    ]

binutils_toolchain = rule(
    implementation = _binutils_toolchain_impl,
    doc = "Expose useful binutils as a toolchain",
    attrs = {
        tool: attr.label(
            mandatory = True,
            executable = True,
            cfg = "exec",
            allow_single_file = True,
            doc = "The {} binary for the execution platform.".format(tool),
        )
        for tool in BINUTILS_TOOLS
    },
)

def _binutils_binary_impl(ctx):
    """Expose one toolchain binutil as an ordinary executable file target.
    """
    binutils = ctx.toolchains[BINUTILS_TOOLCHAIN_TYPE].binutils
    tool = getattr(binutils, ctx.attr.tool).executable

    out = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.symlink(output = out, target_file = tool, is_executable = True)
    return [
        DefaultInfo(
            executable = out,
            runfiles = ctx.runfiles(files = [tool]),
        ),
    ]

binutils_binary = rule(
    implementation = _binutils_binary_impl,
    doc = "One of the binutils toolchain's binaries, as a single executable file target, so that it can be called from a `bazel run`.",
    executable = True,
    attrs = {
        "tool": attr.string(
            mandatory = True,
            values = BINUTILS_TOOLS,
            doc = "Which BinutilsInfo field to expose.",
        ),
    },
    toolchains = [BINUTILS_TOOLCHAIN_TYPE],
)
