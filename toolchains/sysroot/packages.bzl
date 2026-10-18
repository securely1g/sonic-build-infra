"""Pinned Debian inputs shared by GCC and Clang sysroots."""

# Keep these versions in sync with the APT snapshot and imported repositories
# in MODULE.bazel. Both compilers consume the same extracted package bytes.
DEBIAN_SUITES = {
    "trixie": struct(
        gcc_major = "14",
        gcc_version = "14.2.0-19",
        glibc_version = "2.41-12-deb13u3",
        linux_libc_dev_version = "6.12.96-1",
    ),
}

def sysroot_source_archives(suite, architecture):
    """Return existing Distroless source archives, without extraction actions.

    Args:
        suite: Debian suite declared in DEBIAN_SUITES.
        architecture: Debian target architecture, amd64 or arm64.

    Returns:
        Labels of the verified data.tar.xz files in the pinned package repos.
    """
    debian = DEBIAN_SUITES[suite]

    def archive(package, version, from_suite = suite):
        return Label("@{}_{}-{}_{}//:data.tar.xz".format(
            from_suite,
            package.replace("+", "-"),
            architecture,
            version,
        ))

    # Include the matching runtime libraries so development symlinks resolve
    # inside this directory, including when CMake stages the complete sysroot.
    gcc_packages = [
        "gcc-{}-base".format(debian.gcc_major),
        "libgcc-s1",
        "libgcc-{}-dev".format(debian.gcc_major),
        "libstdc++6",
        "libstdc++-{}-dev".format(debian.gcc_major),
        "libasan8",
        "libatomic1",
        "libgomp1",
        "libhwasan0",
        "libitm1",
        "liblsan0",
        "libtsan2",
        "libubsan1",
    ]
    if architecture == "amd64":
        gcc_packages.append("libquadmath0")
    return [
        archive("linux-libc-dev", debian.linux_libc_dev_version, suite + "-security"),
        archive("libc6", debian.glibc_version),
        archive("libc6-dev", debian.glibc_version),
    ] + [archive(package, debian.gcc_version) for package in gcc_packages]
