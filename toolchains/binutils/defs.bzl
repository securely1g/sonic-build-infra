"""Macro for declaring SONiC's binutils toolchains.
"""

load("@rules_cc//cc/toolchains:tool.bzl", "cc_tool")
load("//toolchains/gcc:defs.bzl", "bin")
load("//toolchains/gcc:gcc.bzl", "gcc_repo_name")
load(":binutils_toolchain.bzl", "binutils_toolchain")

def sonic_binutils_toolchain(name, cpu, version, visibility = None, exec_cpu = None, tool_prefix = None, suite = "trixie", target_platform = None):
    """Declares binutils that run on exec_cpu and process files for cpu.

    Args:
        name: Name of the `toolchain()` target.
        cpu: Target `@platforms//cpu` value, e.g. `aarch64`.
        version: The GCC version whose distribution the tools come from, e.g. `14.2.0`.
                 With `cpu` it names the `gcc.debian_toolchain()` tag that fetched it.
        visibility: Visibility of the generated `toolchain()` target.
        exec_cpu: Architecture running the tools; defaults to cpu.
        tool_prefix: Distribution target triple; defaults to the native prefix.
        suite: Debian distribution containing these tools.
        target_platform: Optional target distribution constraint.
    """
    tools = {}
    for tool in ["objcopy", "objdump", "readelf"]:
        source = bin(cpu, version, tool, exec_cpu, tool_prefix, suite)
        cc_tool(
            name = name + "_" + tool,
            src = source,
            data = ["@" + gcc_repo_name(cpu, version, exec_cpu, suite) + "//:multicall_support_files"],
        )
        source = ":" + name + "_" + tool
        tools[tool] = source
    binutils_toolchain(
        name = name + "_binutils",
        objcopy = tools["objcopy"],
        objdump = tools["objdump"],
        readelf = tools["readelf"],
        tags = ["manual"],
    )

    constraints = [
        Label("@platforms//os:linux"),
        Label("@platforms//cpu:" + cpu),
    ]

    if target_platform:
        constraints.append(target_platform)

    native.toolchain(
        name = name,
        exec_compatible_with = [
            Label("@platforms//os:linux"),
            Label("@platforms//cpu:" + (exec_cpu or cpu)),
        ],
        target_compatible_with = constraints,
        toolchain = ":" + name + "_binutils",
        toolchain_type = Label("//toolchains/binutils:toolchain_type"),
        visibility = visibility,
    )
