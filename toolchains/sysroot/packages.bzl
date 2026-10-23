"""Pinned Debian inputs shared by GCC and Clang sysroots."""

# Keep these versions in sync with the APT snapshot and imported repositories
# in MODULE.bazel. Both compilers consume the same extracted package bytes.
DEBIAN_SUITES = {
    "bookworm": struct(
        compiler_version = "12.2.0",
        gcc_major = "12",
        gcc_version = "12.2.0-14-deb12u1",
        glibc_version = "2.36-9-deb12u14",
        linux_libc_dev_version = "6.1.177-1",
    ),
    "trixie": struct(
        compiler_version = "14.2.0",
        gcc_major = "14",
        gcc_version = "14.2.0-19",
        glibc_version = "2.41-12-deb13u3",
        linux_libc_dev_version = "6.12.96-1",
    ),
}

# Fixed Bookworm sysroot package closure.
_BOOKWORM_SYSROOT_REPOS = {
    "amd64": [
        "bookworm_gcc-12-base-amd64_12.2.0-14-deb12u1",
        "bookworm_libasan8-amd64_12.2.0-14-deb12u1",
        "bookworm_libatomic1-amd64_12.2.0-14-deb12u1",
        "bookworm_libc-dev-bin-amd64_2.36-9-deb12u14",
        "bookworm_libc6-amd64_2.36-9-deb12u14",
        "bookworm_libc6-dev-amd64_2.36-9-deb12u14",
        "bookworm_libcom-err2-amd64_1.47.0-2-b2",
        "bookworm_libcrypt-dev-amd64_1-4.4.33-2",
        "bookworm_libcrypt1-amd64_1-4.4.33-2",
        "bookworm_libgcc-12-dev-amd64_12.2.0-14-deb12u1",
        "bookworm_libgcc-s1-amd64_12.2.0-14-deb12u1",
        "bookworm_libgomp1-amd64_12.2.0-14-deb12u1",
        "bookworm-security_libgssapi-krb5-2-amd64_1.20.1-2-deb12u5",
        "bookworm_libitm1-amd64_12.2.0-14-deb12u1",
        "bookworm-security_libk5crypto3-amd64_1.20.1-2-deb12u5",
        "bookworm_libkeyutils1-amd64_1.6.3-2",
        "bookworm-security_libkrb5-3-amd64_1.20.1-2-deb12u5",
        "bookworm-security_libkrb5support0-amd64_1.20.1-2-deb12u5",
        "bookworm_liblsan0-amd64_12.2.0-14-deb12u1",
        "bookworm_libnsl-dev-amd64_1.3.0-2",
        "bookworm_libnsl2-amd64_1.3.0-2",
        "bookworm_libquadmath0-amd64_12.2.0-14-deb12u1",
        "bookworm-security_libssl3-amd64_3.0.20-1_deb12u2",
        "bookworm_libstdc---12-dev-amd64_12.2.0-14-deb12u1",
        "bookworm_libstdc--6-amd64_12.2.0-14-deb12u1",
        "bookworm_libtirpc-common-amd64_1.3.3-ds-1",
        "bookworm_libtirpc-dev-amd64_1.3.3-ds-1",
        "bookworm_libtirpc3-amd64_1.3.3-ds-1",
        "bookworm_libtsan2-amd64_12.2.0-14-deb12u1",
        "bookworm_libubsan1-amd64_12.2.0-14-deb12u1",
        "bookworm-security_linux-libc-dev-amd64_6.1.177-1",
        "bookworm_rpcsvc-proto-amd64_1.4.3-1",
    ],
    "arm64": [
        "bookworm_gcc-12-base-arm64_12.2.0-14-deb12u1",
        "bookworm_libasan8-arm64_12.2.0-14-deb12u1",
        "bookworm_libatomic1-arm64_12.2.0-14-deb12u1",
        "bookworm_libc-dev-bin-arm64_2.36-9-deb12u14",
        "bookworm_libc6-arm64_2.36-9-deb12u14",
        "bookworm_libc6-dev-arm64_2.36-9-deb12u14",
        "bookworm_libcom-err2-arm64_1.47.0-2-b2",
        "bookworm_libcrypt-dev-arm64_1-4.4.33-2",
        "bookworm_libcrypt1-arm64_1-4.4.33-2",
        "bookworm_libgcc-12-dev-arm64_12.2.0-14-deb12u1",
        "bookworm_libgcc-s1-arm64_12.2.0-14-deb12u1",
        "bookworm_libgomp1-arm64_12.2.0-14-deb12u1",
        "bookworm-security_libgssapi-krb5-2-arm64_1.20.1-2-deb12u5",
        "bookworm_libhwasan0-arm64_12.2.0-14-deb12u1",
        "bookworm_libitm1-arm64_12.2.0-14-deb12u1",
        "bookworm-security_libk5crypto3-arm64_1.20.1-2-deb12u5",
        "bookworm_libkeyutils1-arm64_1.6.3-2",
        "bookworm-security_libkrb5-3-arm64_1.20.1-2-deb12u5",
        "bookworm-security_libkrb5support0-arm64_1.20.1-2-deb12u5",
        "bookworm_liblsan0-arm64_12.2.0-14-deb12u1",
        "bookworm_libnsl-dev-arm64_1.3.0-2",
        "bookworm_libnsl2-arm64_1.3.0-2",
        "bookworm-security_libssl3-arm64_3.0.20-1_deb12u2",
        "bookworm_libstdc---12-dev-arm64_12.2.0-14-deb12u1",
        "bookworm_libstdc--6-arm64_12.2.0-14-deb12u1",
        "bookworm_libtirpc-common-arm64_1.3.3-ds-1",
        "bookworm_libtirpc-dev-arm64_1.3.3-ds-1",
        "bookworm_libtirpc3-arm64_1.3.3-ds-1",
        "bookworm_libtsan2-arm64_12.2.0-14-deb12u1",
        "bookworm_libubsan1-arm64_12.2.0-14-deb12u1",
        "bookworm-security_linux-libc-dev-arm64_6.1.177-1",
        "bookworm_rpcsvc-proto-arm64_1.4.3-1",
    ],
}

def sysroot_source_archives(suite, architecture):
    """Return existing Distroless source archives, without extraction actions.

    Args:
        suite: Debian suite declared in DEBIAN_SUITES.
        architecture: Debian target architecture, amd64 or arm64.

    Returns:
        Labels of the verified data.tar.xz files in the pinned package repos.
    """
    if suite == "bookworm":
        return [Label("@" + name + "//:data.tar.xz") for name in _BOOKWORM_SYSROOT_REPOS[architecture]]
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
        "libitm1",
        "liblsan0",
        "libtsan2",
        "libubsan1",
    ]
    if suite == "trixie" or architecture == "arm64":
        gcc_packages.append("libhwasan0")
    if architecture == "amd64":
        gcc_packages.append("libquadmath0")
    return [
        archive("linux-libc-dev", debian.linux_libc_dev_version, suite + "-security" if suite == "trixie" else suite),
        archive("libc6", debian.glibc_version),
        archive("libc6-dev", debian.glibc_version),
    ] + [archive(package, debian.gcc_version) for package in gcc_packages]
