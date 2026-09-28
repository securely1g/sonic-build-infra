"""The toolchain args that name paths, and so have to be resolved per architecture.

We need some post-processing to account for different host, exec and target architectures,
so we centralize that post-processing here.

The only struct we really need is `PLATFORM_DEPENDENT_ARGS`
"""

load("//toolchains/gcc:gcc.bzl", "GCC_METADATA")

def resolve_args(cpu, version, gcc_major, args):
    """Fills in the toolchain's names, leaving the `{package}`s for cc_args.

    Args:
        cpu: the `@platforms//cpu` value, e.g. `x86_64`.
        version: the hermetic GCC version, e.g. `14.2.0`.
        gcc_major: the major version of the GCC that built the sysroot, e.g.
                   `14`. Debian's paths only carry the major.
        args: the arg templates, from PLATFORM_DEPENDENT_ARGS below.

    Returns:
        The args with every `{single}` placeholder filled in.
    """
    arch = GCC_METADATA[cpu]
    return [
        arg.format(
            gcc = arch.gcc,
            gcc_major = gcc_major,
            gcc_version = version,
            multiarch = arch.multiarch,
            dynamic_linker = arch.dynamic_linker,
        )
        for arg in args
    ]

def resolve_packages(sysroot, gcc_repo, args):
    """The packages `args` references, keyed by the name they reference them by.

    Args:
        sysroot: logical package name -> the label of the directory holding it,
                 from the `gcc.debian_toolchain()` tag that declared this
                 toolchain.
        gcc_repo: the GCC distribution repo, for the one directory that does
                  not come from apt.
        args: the resolved args, which name the packages they need.

    Returns:
        The subset of the packages `args` actually references.
    """

    # Every package this toolchain could reach into.
    available = dict(sysroot)

    # The one directory that comes from the GCC distribution rather than from apt.
    available["gcc-builtin"] = gcc_repo + "//:builtin_headers"

    return {
        key: label
        for key, label in available.items()
        if any([("{%s}" % key) in arg for arg in args])
    }

# The args below carry two kinds of placeholder:
#
#   {single}     an architecture's name, filled in here by `resolve_args`.
#   {{doubled}}  a package from PACKAGES. `.format` unescapes it to `{package}`,
#                which cc_args later fills in with that package's directory.

# C compilation includes - use -isystem for libc headers for CGO compatibility
_C_INCLUDES_ARGS = [
    # GCC builtin headers (stddef.h, stdarg.h, etc.) - must come first
    "-isystem",
    "{{gcc-builtin}}/lib/gcc/{gcc}/{gcc_version}/include",

    # https://gcc.gnu.org/bugzilla/show_bug.cgi?id=70129
    # libgcc include path - contains additional GCC headers for #include_next
    "-isystem",
    "{{libgcc-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}/include",

    # Use -isystem for libc headers so CGO can find them (stdlib.h, etc.)
    # These come after GCC headers for proper #include_next resolution
    "-isystem",
    "{{linux-libc-dev}}/usr/include",
    "-isystem",
    "{{linux-libc-dev}}/usr/include/{multiarch}",
    "-isystem",
    "{{libc6-dev}}/usr/include",
    "-isystem",
    "{{libc6-dev}}/usr/include/{multiarch}",

    # Workaround for rules_rust cargo_build_script and rules_foreign_cc
    "-idirafter",
    "../../../../../../../{{libgcc-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}/include",
    "-idirafter",
    "../../../../../../../{{linux-libc-dev}}/usr/include",
    "-idirafter",
    "../../../../../../../{{linux-libc-dev}}/usr/include/{multiarch}",
    "-idirafter",
    "../../../../../../../{{libc6-dev}}/usr/include",
    "-idirafter",
    "../../../../../../../{{libc6-dev}}/usr/include/{multiarch}",
]

# C++ compilation includes - use -idirafter for all headers to support #include_next
_CPP_INCLUDES_ARGS = [
    # GCC builtin headers (stddef.h, stdarg.h, etc.) - must come first
    "-isystem",
    "{{gcc-builtin}}/lib/gcc/{gcc}/{gcc_version}/include",

    # https://gcc.gnu.org/bugzilla/show_bug.cgi?id=70129
    "-idirafter",
    "{{libgcc-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}/include",

    # C++ headers must come before C headers for #include_next to work
    "-idirafter",
    "{{libstdcxx-dev}}/usr/include/c++/{gcc_major}",
    "-idirafter",
    "{{libstdcxx-dev}}/usr/include/{multiarch}/c++/{gcc_major}",

    # Use -idirafter for libc headers in C++ to maintain correct search order for #include_next
    "-idirafter",
    "{{linux-libc-dev}}/usr/include",
    "-idirafter",
    "{{linux-libc-dev}}/usr/include/{multiarch}",
    "-idirafter",
    "{{libc6-dev}}/usr/include",
    "-idirafter",
    "{{libc6-dev}}/usr/include/{multiarch}",

    # Workaround for rules_rust cargo_build_script and rules_foreign_cc
    "-idirafter",
    "../../../../../../../{{libgcc-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}/include",
    "-idirafter",
    "../../../../../../../{{libstdcxx-dev}}/usr/include/c++/{gcc_major}",
    "-idirafter",
    "../../../../../../../{{libstdcxx-dev}}/usr/include/{multiarch}/c++/{gcc_major}",
    "-idirafter",
    "../../../../../../../{{linux-libc-dev}}/usr/include",
    "-idirafter",
    "../../../../../../../{{linux-libc-dev}}/usr/include/{multiarch}",
    "-idirafter",
    "../../../../../../../{{libc6-dev}}/usr/include",
    "-idirafter",
    "../../../../../../../{{libc6-dev}}/usr/include/{multiarch}",
]

_LINK_ARGS = [
    # Debian ships libc.so/libm.so as GNU ld scripts (GROUP/AS_NEEDED) that name
    # the real .so files by absolute host path, e.g. /lib/x86_64-linux-gnu/libc.so.6.
    #
    # Usually, this would be handled by `rules_distroless`.
    #
    # However, we don't depend on the rules_distroless-generated targets to build the sysroot,
    # and instead we depend on `//:directory` targets.
    # Therefore, we lose out on the automated fixes, and must add them by hand.
    "-Wl,--remap-inputs=/lib/{multiarch}/libc.so.6={{libc6}}/usr/lib/{multiarch}/libc.so.6",
    "-Wl,--remap-inputs=/lib/{multiarch}/libm.so.6={{libc6}}/usr/lib/{multiarch}/libm.so.6",
    "-Wl,--remap-inputs=/lib/{multiarch}/libmvec.so.1={{libc6}}/usr/lib/{multiarch}/libmvec.so.1",
    "-Wl,--remap-inputs=/{dynamic_linker}={{libc6}}/usr/{dynamic_linker}",
    "-Wl,--remap-inputs=/usr/lib/{multiarch}/libc_nonshared.a={{libc6-dev}}/usr/lib/{multiarch}/libc_nonshared.a",
    "-B",
    "{{libc6}}/lib/{multiarch}",
    "-Wl,-rpath-link=/lib/{multiarch}",
    "-B",
    "{{libc6-dev}}/usr/lib/{multiarch}",
    "-L",
    "{{libgcc-s1}}/lib/{multiarch}",
    "-L",
    "{{libstdcxx-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}",

    # For rules_foreign_cc and rules_rust to work after changing its workdir
    "-B",
    "../../../../../../../{{libc6}}/lib/{multiarch}",
    "-B",
    "../../../../../../../{{libc6-dev}}/usr/lib/{multiarch}",
    "-L",
    "../../../../../../../{{libgcc-s1}}/lib/{multiarch}",
    "-L",
    "../../../../../../../{{libgcc-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}",
    "-L",
    "../../../../../../../{{libstdcxx-dev}}/usr/lib/gcc/{multiarch}/{gcc_major}",

    # Link libc_nonshared.a to resolve atexit and other startup functions
    # Use -u to pull in specific symbols without --whole-archive (which causes duplicate definition errors
    # when multiple -B paths point to the same directory)
    "-Wl,-u,atexit",
    "-Wl,-u,at_quick_exit",
    "-l:libc_nonshared.a",
]

PLATFORM_DEPENDENT_ARGS = [
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
