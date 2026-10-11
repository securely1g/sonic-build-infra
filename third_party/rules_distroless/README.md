# Distroless compatibility patches and checks

The build uses the published `rules_distroless` **0.9.4.sonic.1** module, which
includes the existing Protobuf `.inc` header fix. The compiled test under
`tests/` verifies that public headers remain available.

APT package selection uses the SONiC selector with public `:data`/`:control`
inputs and ordinary Distroless `flatten`. See [the APT adapter](../../apt/README.md).

The source regressions apply the patches below together to one checksum-pinned
upstream 0.9.4 archive. This checks their combined behavior without changing the
production module selection. Registry publication supplies consumer versions.

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

## pkg-config argument whitespace

`0002-pkgconfig-whitespace.patch` is a SONiC-maintained fix against the upstream
parser. Debian Abseil metadata can contain repeated spaces or empty optional
substitutions; forwarding empty arguments makes gRPC/DASH links fail. The patch
handles spaces and tabs in `Libs`, `Libs.private` and `Cflags` while preserving
the original parser attribution. `pkgconfig_whitespace_test` checks empty
metadata and retained flags against the same patched archive as the metadata
export regression.
