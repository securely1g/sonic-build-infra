"""Fetches a GCC distribution and overlays BUILD files on top
"""

# The CPU goes by different names in different places.
# This dict captures those differences, and adding an architecture means adding
# an entry here plus its integrity in //:MODULE.bazel.
#
# gcc:       the gcc prefix for the arch, e.g. x86_64-linux.
# deb:       the Debian package architecture, e.g. libc6_2.36-9_amd64.deb.
# multiarch: the Debian multiarch triple, e.g. /usr/lib/x86_64-linux-gnu.
# tarball:   the arch as our GCC release names it. Not uniform: the x86_64
#            build is `x86_64`, but the aarch64 one spells out its host.
GCC_METADATA = {
    "x86_64": struct(
        gcc = "x86_64-linux",
        deb = "amd64",
        multiarch = "x86_64-linux-gnu",
        dynamic_linker = "lib64/ld-linux-x86-64.so.2",
        tarball = "x86_64",
    ),
    "aarch64": struct(
        gcc = "aarch64-linux",
        deb = "arm64",
        multiarch = "aarch64-linux-gnu",
        dynamic_linker = "lib/ld-linux-aarch64.so.1",
        tarball = "aarch64-host-aarch64",
    ),
}

def gcc_repo_name(cpu, version):
    """The repo the `gcc` extension fetches one distribution into.

    Args:
        cpu: The `@platforms//cpu` value, e.g. `x86_64`.
        version: The GCC version, e.g. `14.2.0`.

    Returns:
        The bare repo name, e.g. `gcc-14.2.0-linux-target-x86_64-host-x86_64`.
    """

    if cpu not in GCC_METADATA:
        fail("gcc_repo_name() names arch {}, which GCC_METADATA does not describe. Known: {}".format(
            cpu,
            sorted(GCC_METADATA),
        ))
    return "gcc-{version}-linux-target-{cpu}-host-{cpu}".format(cpu = cpu, version = version)

def _download_gcc(rctx):
    rctx.download_and_extract(
        url = rctx.attr.urls,
        integrity = rctx.attr.integrity,
    )
    target_arch = rctx.attr.target_arch
    rctx.delete("sysroot")

    rctx.template(
        "BUILD.bazel",
        rctx.attr.build_file_template,
        substitutions = {
            "{target_arch}": target_arch,
            "{version}": rctx.attr.version,
        },
        executable = False,
    )

fetch_gcc = repository_rule(
    implementation = _download_gcc,
    attrs = {
        "urls": attr.string_list(),
        "integrity": attr.string(),
        "target_arch": attr.string(
            default = "x86_64",
            values = sorted(GCC_METADATA),
        ),
        "version": attr.string(mandatory = True),
        "build_file_template": attr.label(default = "//toolchains/gcc:gcc.BUILD"),
    },
)
