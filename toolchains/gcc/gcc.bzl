"""Architecture names and repository names shared by the Debian GCC toolchains."""

# The CPU goes by different names in different places.
# This dict captures those differences, and adding an architecture means adding
# an entry here plus pinned packages in //:MODULE.bazel.
#
# gcc:       the Debian GCC prefix, e.g. x86_64-linux-gnu.
# deb:       the Debian package architecture, e.g. libc6_2.36-9_amd64.deb.
# multiarch: the Debian multiarch triple, e.g. /usr/lib/x86_64-linux-gnu.
GCC_METADATA = {
    "x86_64": struct(
        gcc = "x86_64-linux-gnu",
        deb = "amd64",
        multiarch = "x86_64-linux-gnu",
        dynamic_linker = "lib64/ld-linux-x86-64.so.2",
    ),
    "aarch64": struct(
        gcc = "aarch64-linux-gnu",
        deb = "arm64",
        multiarch = "aarch64-linux-gnu",
        dynamic_linker = "lib/ld-linux-aarch64.so.1",
    ),
}

def gcc_repo_name(cpu, version, exec_cpu = None):
    """The repo the `gcc` extension fetches one distribution into.

    Args:
        cpu: The `@platforms//cpu` value, e.g. `x86_64`.
        version: The GCC version, e.g. `14.2.0`.
        exec_cpu: Architecture running the tools; defaults to the target CPU.

    Returns:
        The bare repo name, e.g. `gcc-14.2.0-linux-target-x86_64-host-x86_64`.
    """

    if cpu not in GCC_METADATA:
        fail("gcc_repo_name() names arch {}, which GCC_METADATA does not describe. Known: {}".format(
            cpu,
            sorted(GCC_METADATA),
        ))
    return "gcc-{version}-linux-target-{cpu}-host-{exec_cpu}".format(
        cpu = cpu,
        exec_cpu = exec_cpu or cpu,
        version = version,
    )
