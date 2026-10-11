# Distroless compatibility patches and checks

The build uses the published `rules_distroless` **0.9.4.sonic.1** module, which
includes the existing Protobuf `.inc` header fix. The compiled test under
`tests/` verifies that public headers remain available.

`0001-pkgconfig-whitespace.patch` fixes the upstream 0.9.4 pkg-config parser:
repeated spaces and optional empty substitutions must not become empty linker
input filenames. Debian's Abseil development packages expose this through the
public gRPC C++ dependency target. Tabs now separate flags consistently in
`Libs`, `Libs.private` and `Cflags`. The patch retains upstream attribution and
records its source and reason in its header.

The `pkgconfig_test` applies this patch to the checksum-pinned upstream archive
and checks the actual Starlark parser, including empty metadata, repeated spaces,
tabs and preserved flags. Native AMD64 and ARM64 CI run the regression. The
registry owns publication as `rules_distroless` 0.9.4.sonic.2; this repository owns
the patch. No SONiC-specific package resolver is introduced.

APT package selection uses the SONiC selector with public `:data`/`:control`
inputs and ordinary Distroless `flatten`. See [the APT adapter](../../apt/README.md).
