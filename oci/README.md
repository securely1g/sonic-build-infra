# OCI debug layers

`debug_symbols_layer` collects source-owned symbols through the runtime build
graph. Use `imported_debug_symbols_layer` for symbols supplied by existing
packages whose producer cannot expose `DebugSymbolsInfo`.

```starlark
load("@sonic-build-infra//oci:imported_debug_symbols_layer.bzl", "imported_debug_symbols_layer")

imported_debug_symbols_layer(
    name = "base_debug_symbols",
    runtime = ":image",
    tars = [":base_debug_package"],
    expected_platform = "linux/amd64",
    required_paths = ["usr/lib/x86_64-linux-gnu/libsonicdbcli.so.0.0.0"],
)
```

Here `:image` is the **final runtime OCI image**, and `:base_debug_package`
provides an extracted debug-package TAR. If a later runtime layer replaces
`libswsscommon` but retains the base's `libsonicdbcli`, only `libsonicdbcli`'s
matching imported companion is selected. No package-specific build-ID snapshot
is needed. A base rebuild with no matching companion fails the required-path
check, rather than silently losing that library's debug coverage.

The matcher validates the complete OCI layout's blob hashes and platform,
applies runtime layers and whiteouts, and compares each selected companion's
ELF class, byte order, machine and build ID. The runtime's GNU debuglink filename
and CRC must match. Companions must contain DWARF; GNU debugaltlink references
also require their matching DWZ supplements. Files for absent or replaced
runtimes and package documentation are excluded. Candidate symbol files must
be regular files under `usr/lib/debug/`; their ownership, modes and contents
are preserved, with deterministic timestamps. Input packages are never built
or installed, and no maintainer script or target program is executed.

The default output is the selected TAR. The `receipt` output group records the
runtime manifest digest, input archive hashes, each runtime/companion match,
DWZ identities, excluded paths and output hash. Keep the importer's own package
receipt alongside this receipt to establish the original DEB provenance.
`required_paths` names runtime files that must retain coverage. Other unmatched
runtime files are outside this rule's required coverage and may need their own
source-owned symbol collector or an explicit image coverage check.

`//oci:lib` exposes the shared `sonic_oci.layout.validate_layout` and
`sonic_oci.elf.inspect_elf` readers for other declared-input assembly tools.

Validation: `//oci:imported_symbols_test` covers changed runtime files, missing
coverage, whiteouts, mismatched CRC/build IDs/architecture, DWZ supplements,
malformed ELF metadata and corrupt OCI blobs. `//oci:native_symbols_test` uses
real compiled and stripped runtime/symbol outputs. Neither creates DEBs.
