# Distroless compatibility check

The build uses the published `rules_distroless` **0.9.4-sonic.1** module, which
includes the existing Protobuf `.inc` header fix. The compiled test under
`tests/` verifies that public headers remain available.

APT package selection uses the SONiC selector with public `:data`/`:control`
inputs and ordinary Distroless `flatten`. No additional Distroless patch or
archive override is needed. See [the APT adapter](../../apt/README.md).

## Original pkg-config metadata

`0001-pkgconfig-files.patch` exposes the `.pc` files already discovered in each
Debian package. These files describe a library's version, include paths and link
options. Native configure scripts, including iproute2's, need to ask pkgconf
about the same packages that provide their Bazel C/C++ dependencies.

The patch adds public `:pkgconfig` targets to each imported package, each
architecture-selected package alias, and the APT dependency-set root. For
example, `@iproute2_deps//:pkgconfig` includes the original metadata for that
set and its dependencies on the selected architecture. It never combines
AMD64 and ARM64 metadata. Existing `:data` and library targets keep their
interfaces. Alternate `.pc` symlinks, such as Lua's `lua-5.1.pc`, are exported
as well; aliases are not reparsed to alter the existing C/C++ imports.

Consumers declare this target as build data and use the declared pkgconf tool.
They can relocate pkgconf's `prefix`, `libdir` and `includedir` variables to the
already staged dependency files. The patch does not rewrite the original `.pc`
contents or construct another target filesystem.

The source regression tests architecture selection against the patched,
checksum-pinned upstream archive. The iproute2 source producer exercises the
export through its original configure checks for libmnl, libbpf, xtables,
SELinux, libcap, libtirpc and libbsd. Registry validation must also preserve the
metadata bytes and exercise the generated public targets on native AMD64 and
ARM64 before declaring this integration complete.
## Prefer concrete packages during resolution

`0001-concrete-packages.patch` changes the upstream 0.9.4 dependency resolver to
try a package's own name before its virtual providers. The concrete candidate
must satisfy the requested version, architecture and suite. When none matches,
the existing virtual-provider selection remains available.

This is needed by Syncd's debug image: `vim (= 2:9.1.1230-2)` must select the
concrete Vim package, allowing its original control metadata and payload to be
validated together. Upstream currently selects `vim-nox` through `Provides`.
The registry publishes this patch with the existing compatibility fixes as
`rules_distroless` 0.9.4.sonic.5. This source PR does not change the module used by
other consumers or remove the older shared Vim workaround.

The `concrete_packages_test` runs the actual upstream package index parser and
resolver against both AMD64 and ARM64 records. It checks exact and unpinned
concrete requests, version/suite filtering, virtual-only fallback and
Architecture: all candidates. The unchanged resolver fails the same regression.
No package download, extraction or DEB production occurs in this test.
