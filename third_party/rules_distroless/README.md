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
## Architecture-specific development headers

`0001-multiarch-headers.patch` fixes an upstream 0.9.4 assumption that every
pkg-config dependency needs the AMD64 include directory. Lua 5.1 on ARM64 instead
places `lua5.1-deb-multiarch.h` under `/usr/include/aarch64-linux-gnu`; without that
search path, the existing SONiC eventd rsyslog tests cannot compile.

The patch derives additional include roots from headers actually declared by
the Debian package. It preserves ordinary `/usr/include` and supports ARM64,
AMD64 and armhf layouts without borrowing host headers. `multiarch_test` applies
the patch to the checksum-pinned upstream archive and checks those layouts.
Registry publication composes this with the existing include-fragment and
pkg-config whitespace patches and compiles a real Lua consumer on native AMD64
and ARM64. No Debian package is created by these regressions.
