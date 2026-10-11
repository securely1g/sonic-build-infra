# Distroless compatibility check

The build uses the published `rules_distroless` **0.9.4-sonic.1** module, which
includes the existing Protobuf `.inc` header fix. The compiled test under
`tests/` verifies that public headers remain available.

APT package selection uses the SONiC selector with public `:data`/`:control`
inputs and ordinary Distroless `flatten`. No additional Distroless patch or
archive override is needed. See [the APT adapter](../../apt/README.md).

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
