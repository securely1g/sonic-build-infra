"""The toolchain args that name paths, and so have to be resolved per architecture.

We need some post-processing to account for different host, exec and target architectures,
so we centralize that post-processing here.

The only struct we really need is `PLATFORM_DEPENDENT_ARGS`
"""

load("//toolchains/gcc:gcc.bzl", "GCC_METADATA")

def resolve_args(cpu, version, gcc_major, args, tool_prefix = None):
    """Fills in the toolchain's names, leaving directory placeholders for cc_args.

    Args:
        cpu: the `@platforms//cpu` value, e.g. `x86_64`.
        version: the hermetic GCC version, e.g. `14.2.0`.
        gcc_major: the major version of the GCC that built the sysroot, e.g.
                   `14`. Debian's paths only carry the major.
        args: the arg templates, from PLATFORM_DEPENDENT_ARGS below.
        tool_prefix: Compiler distribution target triple, independent of Debian paths.

    Returns:
        The args with every `{single}` placeholder filled in.
    """
    arch = GCC_METADATA[cpu]
    return [
        arg.format(
            gcc = tool_prefix or arch.gcc,
            gcc_major = gcc_major,
            gcc_version = version,
            multiarch = arch.multiarch,
            dynamic_linker = arch.dynamic_linker,
        )
        for arg in args
    ]

def resolve_inputs(sysroot, gcc_repo, args):
    """Select declared directories referenced by an action's arguments.

    Args:
        sysroot: Label of the shared repository sysroot directory.
        gcc_repo: Repository containing this compiler's private files.
        args: Argument templates after resolving architecture/version fields.

    Returns:
        The directory labels referenced by cc_args format placeholders.
    """
    available = {
        "sysroot": sysroot,
        "gcc-programs": gcc_repo + "//:compiler_programs",
    }
    return {
        key: label
        for key, label in available.items()
        if any([("{%s}" % key) in arg for arg in args])
    }

# {single} placeholders are architecture/version fields. {{doubled}} ones
# become directory placeholders resolved by cc_args with declared inputs.
# GCC builtin headers and system headers come from the shared Debian sysroot.
_C_INCLUDES_ARGS = [
    "-isystem",
    "{{sysroot}}/usr/lib/gcc/{multiarch}/{gcc_major}/include",
    "-isystem",
    "{{sysroot}}/usr/include",
    "-isystem",
    "{{sysroot}}/usr/include/{multiarch}",
]

# C++ headers precede C headers for #include_next. Both rules_foreign_cc and
# cargo_build_script make -isystem paths absolute before changing directory;
# no fixed-depth relative fallback or package-specific layout is needed.
_CPP_INCLUDES_ARGS = [
    "-isystem",
    "{{sysroot}}/usr/lib/gcc/{multiarch}/{gcc_major}/include",
    "-isystem",
    "{{sysroot}}/usr/include/c++/{gcc_major}",
    "-isystem",
    "{{sysroot}}/usr/include/{multiarch}/c++/{gcc_major}",
    "-isystem",
    "{{sysroot}}/usr/include",
    "-isystem",
    "{{sysroot}}/usr/include/{multiarch}",
]

_LINK_ARGS = [
    # Select the target architecture's startup files and development libraries
    # explicitly from the shared multiarch sysroot.
    "-B",
    "{{sysroot}}/usr/lib/{multiarch}/",
    "-B",
    "{{sysroot}}/usr/lib/gcc/{multiarch}/{gcc_major}/",
    "-L",
    "{{sysroot}}/usr/lib/gcc/{multiarch}/{gcc_major}",
    "-L",
    "{{sysroot}}/usr/lib/{multiarch}",
]

PLATFORM_DEPENDENT_ARGS = [
    struct(
        suffix = "_programs",
        actions = [
            "@rules_cc//cc/toolchains/actions:c_compile",
            "@rules_cc//cc/toolchains/actions:cpp_compile_actions",
            "@rules_cc//cc/toolchains/actions:assembly_actions",
            "@rules_cc//cc/toolchains/actions:link_actions",
        ],
        # collect2 searches directories, even when the GCC driver accepts a
        # filename prefix. Declare the distribution's unprefixed aliases and
        # use their directory so both stages choose the downloaded programs.
        args = ["-B", "{{gcc-programs}}/{gcc}/bin/"],
    ),
    struct(
        suffix = "_c_includes",
        actions = ["@rules_cc//cc/toolchains/actions:c_compile"],
        args = _C_INCLUDES_ARGS,
    ),
    struct(
        suffix = "_cpp_includes",
        actions = ["@rules_cc//cc/toolchains/actions:cpp_compile_actions"],
        args = _CPP_INCLUDES_ARGS,
    ),
    struct(
        suffix = "_links",
        actions = [
            "@rules_cc//cc/toolchains/actions:link_actions",
            "@rules_cc//cc/toolchains/actions:link_executable_actions",
        ],
        args = _LINK_ARGS,
    ),
]
