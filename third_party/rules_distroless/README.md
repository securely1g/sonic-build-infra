# Distroless compatibility check

The build uses the published `rules_distroless` **0.9.4-sonic.1** module, which
includes the existing Protobuf `.inc` header fix. The compiled test under
`tests/` verifies that public headers remain available.

APT package selection uses the SONiC selector with public `:data`/`:control`
inputs and ordinary Distroless `flatten`. No additional Distroless patch or
archive override is needed. See [the APT adapter](../../apt/README.md).
