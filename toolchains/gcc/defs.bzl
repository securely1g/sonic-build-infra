"""Macro for declaring SONiC's GCC toolchains.

Every toolchain is a host toolchain, which means that it can only execute in the platform it targets.
Cross-compilation is easy to add later if we need to.
"""

load("@rules_cc//cc/toolchains:args.bzl", "cc_args")
load("@rules_cc//cc/toolchains:tool.bzl", "cc_tool")
load("@rules_cc//cc/toolchains:tool_map.bzl", "cc_tool_map")
load("@rules_cc//cc/toolchains:toolchain.bzl", "cc_toolchain")
load("//toolchains/gcc:gcc.bzl", "GCC_METADATA", "gcc_repo_name")
load(
    "//toolchains/gcc/args:platform_dependent_args.bzl",
    "PLATFORM_DEPENDENT_ARGS",
    "resolve_args",
    "resolve_packages",
)

_PLATFORM_DEPENDENT_ARGS = PLATFORM_DEPENDENT_ARGS
_PLATFORM_INDEPENDENT_ARGS = [
    Label("//toolchains/gcc/args:nostdlib"),
    Label("//toolchains/gcc/args:nostdinc"),
    Label("//toolchains/gcc/args:nostdinc++"),
    Label("//toolchains/gcc/args:add_build_ids"),
    Label("//toolchains/args:no_absolute_paths_for_builtins"),
    Label("//toolchains/args:warnings"),
    Label("//toolchains/args:dpkg_buildflags"),
    Label("//toolchains/args:dpkg_buildflags_link"),
    Label("//toolchains/args:as_needed"),
]

_FEATURES = [
    Label("//toolchains/gcc:external_include_paths"),
    Label("@rules_cc//cc/toolchains/args:experimental_replace_legacy_action_config_features"),
]

_GCC_CAPABILITIES = [Label("@rules_cc//cc/toolchains/capabilities:supports_pic")]

def bin(cpu, version, tool):
    """A tool from one GCC distribution, e.g. its `objcopy`.

    Args:
        cpu: The `@platforms//cpu` the distribution runs on, e.g. `x86_64`.
        version: The GCC version, e.g. `14.2.0`. With `cpu` it names the
                 `gcc.debian_toolchain()` tag that fetched the distribution.
        tool: The unprefixed tool name, e.g. `objcopy`.

    Returns:
        The label of that tool in the distribution's repo.
    """
    return "@{repo}//:bin/{prefix}-{tool}".format(
        repo = gcc_repo_name(cpu, version),
        prefix = GCC_METADATA[cpu].gcc,
        tool = tool,
    )

def sonic_host_toolchain(
        name,
        cpu,
        version,
        debian_gcc_major,
        sysroot,
        target_platform,
        visibility = None):
    """Declares a GCC toolchain where `exec == target == $cpu`.

    Called from `gcc.debian_toolchain()`.

    This is a legacy macro because cc_toolchain creates
    targets that are incompatible with symbolic macros.

    Args:
        name: Name of the `toolchain()` target.
              This is what `register_toolchains` refers to.
        cpu: The `@platforms//cpu` value this toolchain both runs on and targets,
              e.g. `x86_64`.
        version: The GCC version this toolchain compiles with, e.g. `14.2.0`.
                 Together with `cpu` it names the `gcc.debian_toolchain()` tag
                 in //:MODULE.bazel this toolchain is built from, and so the
                 distribution every tool is taken from.
        debian_gcc_major: Major version of the GCC that built the sysroot, e.g. `14`.
                          Debian's paths only carry the major.
        sysroot: Map of package_name -> Label of its //:directory, to build the Debian based sysroot.
        target_platform: The constraint_value() target specifying the environment we'll deploy to.
                         e.g. //platforms:trixie.
        visibility: Visibility of the generated `toolchain()` target.
    """

    # The repo the `gcc` extension fetched this (version, cpu)'s distribution
    # into. Derived rather than passed in, so it cannot name a different
    # toolchain than `version` and `cpu` resolve the args from.
    gcc_repo = "@" + gcc_repo_name(cpu, version)

    def _bin(tool):
        return bin(cpu, version, tool)

    # Support files the compiler and linker need alongside the driver.
    native.alias(
        name = name + "_multicall_support_files",
        actual = gcc_repo + "//:multicall_support_files",
        tags = ["manual"],
    )
    native.alias(
        name = name + "_linker_builtins",
        actual = gcc_repo + "//:linker_builtins",
        tags = ["manual"],
    )

    # Using the unprefixed `bin/gcc` trips rules_cc's toolchain-include check.
    # https://github.com/bazelbuild/rules_cc/issues/277
    cc_tool(
        name = name + "_gcc",
        src = _bin("gcc"),
        capabilities = _GCC_CAPABILITIES,
        data = [name + "_multicall_support_files"],
        tags = ["manual"],
    )
    cc_tool(
        name = name + "_g++",
        src = _bin("g++"),
        capabilities = _GCC_CAPABILITIES,
        data = [name + "_multicall_support_files"],
        tags = ["manual"],
    )

    # gcc drives the link, so it needs the linker's support files too.
    cc_tool(
        name = name + "_ld",
        src = _bin("g++"),
        data = [
            name + "_linker_builtins",
            name + "_multicall_support_files",
        ],
        tags = ["manual"],
    )
    cc_tool(
        name = name + "_ar",
        src = _bin("gcc-ar"),
        tags = ["manual"],
    )
    cc_tool(
        name = name + "_objcopy",
        src = _bin("objcopy"),
        tags = ["manual"],
    )
    cc_tool(
        name = name + "_strip",
        src = _bin("strip"),
        tags = ["manual"],
    )

    cc_tool_map(
        name = name + "_tools",
        tags = ["manual"],
        tools = {
            "@rules_cc//cc/toolchains/actions:ar_actions": name + "_ar",
            "@rules_cc//cc/toolchains/actions:assembly_actions": name + "_gcc",
            "@rules_cc//cc/toolchains/actions:c_compile": name + "_gcc",
            "@rules_cc//cc/toolchains/actions:cpp_compile_actions": name + "_g++",
            "@rules_cc//cc/toolchains/actions:link_actions": name + "_ld",
            "@rules_cc//cc/toolchains/actions:objcopy_embed_data": name + "_objcopy",
            "@rules_cc//cc/toolchains/actions:strip": name + "_strip",
        },
    )

    # The args name the packages they need, and resolve_packages hands cc_args
    # exactly those -- it fails on a package that goes unreferenced.
    for spec in _PLATFORM_DEPENDENT_ARGS:
        args = resolve_args(cpu, version, debian_gcc_major, spec.args)
        packages = resolve_packages(sysroot, gcc_repo, args)

        cc_args(
            name = name + spec.suffix,
            actions = spec.actions,
            args = args,
            data = packages.values(),
            format = packages,
            visibility = ["//visibility:public"],
        )

    platform_dependent_args = [
        ":" + name + spec.suffix
        for spec in _PLATFORM_DEPENDENT_ARGS
    ]

    cc_toolchain(
        name = name + "_cc_toolchain",
        args = _PLATFORM_INDEPENDENT_ARGS + platform_dependent_args,
        compiler = "gcc",
        enabled_features = _FEATURES,
        known_features = _FEATURES,
        tags = ["manual"],
        tool_map = name + "_tools",
    )

    # A host toolchain runs on the same platform it targets, so exec and target
    # take the same constraints.
    constraints = [
        Label("@platforms//os:linux"),
        Label("@platforms//cpu:" + cpu),
    ]

    native.toolchain(
        name = name,
        exec_compatible_with = constraints,
        target_compatible_with = constraints + [target_platform],
        toolchain = name + "_cc_toolchain",
        toolchain_type = Label("@bazel_tools//tools/cpp:toolchain_type"),
        visibility = visibility,
    )
