# Bookworm target toolchains

Public platforms: `//platforms:x86_64_bookworm`, `//platforms:aarch64_bookworm`.
They select source-native Cc actions with fixed Debian GCC12 and an independent
Bookworm glibc2.36 sysroot. Linux AMD64 execution can target both architectures;
ARM64 execution has a declared native compiler distribution.

Bookworm keeps GCC12, its compiler support files and its glibc2.36 target
sysroot. Linking uses the architecture-matched declared Trixie BFD launcher:
Distroless preserves Debian linker scripts and supplies `--remap-inputs` so their
absolute paths resolve to declared package files; Bookworm's BFD2.40 cannot read
that option. The Trixie launcher brings its own execution loader and libraries,
separately from the Bookworm target ABI. Compiler action inputs and runfiles
include that complete linker runtime. The compiler's `inputs.json` records the
selected linker and support-files labels.

Components declare target APT packages through the shared extension namespace:

```starlark
apt_bookworm = use_extension("@sonic_build_infra//toolchains/bookworm:apt.bzl", "apt")
apt_bookworm.install(
    dependency_set = "component_bookworm",
    packages = ["libc6"],
    suites = ["bookworm", "bookworm-updates", "bookworm-security"],
)
use_repo(apt_bookworm, "component_bookworm")
```

The extension reuses the pinned rules_distroless implementation. Its separate
namespace prevents identical package versions in Bookworm from overwriting
Trixie package source identity. Infrastructure owns the fixed sources; target
packages select the complete main/updates/security suite set used by the builder.
Execution tools remain independent and can use the existing Trixie tool runtime.

Snapshot and package URLs, versions and integrity values are declared in
`MODULE.bazel`; compiler and sysroot package sets are declared in
`toolchains/gcc/debian.bzl` and `toolchains/sysroot/packages.bzl`. Absolute
root-internal Debian loader aliases become relative links inside declared extracted repositories.
Native and cross compiler helpers always use the execution architecture loader.

Bookworm default compiler hardening follows dpkg1.21.23. Trixie retains its
existing stack-clash and CPU branch-protection defaults. The existing shared
bindnow linker policy applies to both suites; component-specific original
hardening settings belong in their native target attributes for the selected suite.

Regression fixtures in `tests` compile with GCC12, assert target ELF ABI and
symbol requirements, and execute native programs using declared Bookworm libc,
libstdc++ and libgcc payloads. ARM64 targets are compiled and inspected on AMD64.
