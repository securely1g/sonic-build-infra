# Debian sysroot assembly

Load `debian_sysroot` from `@sonic_build_infra//sysroot:repositories.bzl` in a
WORKSPACE, or with `use_repo_rule` in a MODULE.bazel:

```starlark
debian_sysroot = use_repo_rule("@sonic_build_infra//sysroot:repositories.bzl", "debian_sysroot")
debian_sysroot(
    name = "target_sysroot",
    packages = [
        # do not sort: later package archives overlay earlier ones
        "@target_libc//:data.tar.xz",
        "@target_libc_dev//:data.tar.xz",
    ],
)
```

The caller selects and verifies the package archives, including their target
architecture, versions, and Debian snapshot. The assembler extracts `.tar.xz`
package data archives in the supplied order. It provides the public `:files`
filegroup and the `:.sysroot-root` marker for toolchains. Missing `bin`, `lib`,
and `sbin` paths receive relative usrmerge aliases when the corresponding
`usr/` directories exist; absolute symlinks beneath `usr` are rejected.

The host must provide `ln` and `find`. Package selection, compiler registration,
platforms, and emulator wiring remain with the consumer.
