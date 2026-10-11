# Distroless compatibility check

The build uses the published `rules_distroless` **0.9.4-sonic.1** module, which
includes the existing Protobuf `.inc` header fix. The compiled test under
`tests/` verifies that public headers remain available.

APT package selection uses the SONiC selector with public `:data`/`:control`
inputs and ordinary Distroless `flatten`. No additional Distroless patch or
archive override is needed. See [the APT adapter](../../apt/README.md).

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
