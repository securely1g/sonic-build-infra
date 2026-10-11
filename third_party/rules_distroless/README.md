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

## Multiarch header directories

`0003-multiarch-headers.patch` derives include roots from the package's declared
headers instead of adding the AMD64 path on every CPU. This lets the ARM64
eventd/rsyslog consumer find Lua's `lua5.1-deb-multiarch.h`, while retaining
ordinary `/usr/include`. `multiarch_test` checks the original header layouts.

Related public work is Song Qing's [commit 3b26479](https://github.com/bazel-contrib/rules_distroless/commit/3b26479ff8eca6ac8b69c1a79ff47bc5a9d8bbc0)
in [upstream #220](https://github.com/bazel-contrib/rules_distroless/pull/220),
which was closed without merging. It uses the same header-inventory approach
in a different importer structure; this is a narrower 0.9.4 compatibility
implementation, not a direct cherry-pick of that commit.

## Concrete packages before virtual providers

`0004-concrete-packages.patch` prefers a matching package by its own name before
considering `Provides` entries. For example, Syncd's debug image needs the real
`vim` package and its control metadata, rather than an unintended `vim-nox`
provider. Version, architecture and suite constraints still apply; virtual
providers remain the fallback. `concrete_packages_test` exercises the original
index parser and resolver with both architecture records and `Architecture: all`.

Upstream [issue #212](https://github.com/bazel-contrib/rules_distroless/issues/212)
by jseba includes a public patch for the same priority policy against an older
API. [Issue #265](https://github.com/bazel-contrib/rules_distroless/issues/265) by
wiest proposes a narrower provider guard. This patch reorders the existing 0.9.4
resolver while retaining its full constraints and fallback behavior; these are
related proposals, not imported upstream commits.
