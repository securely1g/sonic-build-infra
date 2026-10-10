# Import existing Debian packages

`deb_import` accepts existing `.deb` file labels in the caller's installation
order. For example, a Make prerequisite can supply a package file while Bazel
owns its extraction and every subsequent image action:

```starlark
load("@sonic_build_infra//deb:deb_import.bzl", "deb_import")

deb_import(
    name = "runtime_packages",
    srcs = ["//:target/debs/trixie/example_1.0-1_amd64.deb"],
    architecture = "amd64",
    metadata = {"image": "docker-example", "variant": "runtime"},
)
filegroup(
    name = "runtime_package_metadata",
    srcs = [":runtime_packages"],
    output_group = "manifest",
)
```

The default output is `runtime_packages/payload.tar`. Its entries preserve the
original names, contents, modes, UID/GID, owner names, timestamps, symlinks,
hardlinks and PAX headers. Package order is preserved, including repeated paths
across packages: the later package's entry wins when an image layer is applied.
No source package is built, no host `dpkg` or `ar` is called, and no maintainer
script runs. This is payload import, not full Debian installation. Consumers
still own required installation state, path normalization and image policy.

The `manifest` output group supplies `runtime_packages/manifest.json`:

- `schema: 1`, the requested architecture, caller metadata and ordered `packages`.
- Each package's actual name, version, architecture, complete `control_fields`
  (including `Depends`, `Pre-Depends` and multiline descriptions), SHA-256 hashes
  of all `control_files`, and a hash of its original decompressed control TAR.
- The original `source_deb` filename, declared `source_label`, SHA-256 and size;
  plus the original decompressed data TAR's hash, size and member count.
- The aggregate `payload` filename, SHA-256, size and member count.

The `controls` group exposes the original decompressed control TARs. They retain
maintainer scripts for inspection; importing never executes those scripts.
`DebImportInfo` exposes the same `payload`, `manifest` and `controls` files to
other Starlark rules. Caller metadata cannot replace verified reserved fields.
`required_packages` in metadata is checked against extracted package names.

Pass a parent's manifest as `runtime_manifest` for a debug import. The importer
records that file's SHA-256 and rejects a repeated package if its original DEB
bytes differ. This supports downstream APT selection using the precise retained
package controls, without treating caller context as producer provenance.

The reader supports Debian 2.0 archives containing uncompressed, gzip, xz or
bzip2 control/data TARs. Unsupported compression (including zstd), malformed
archives, duplicate package names, foreign architectures, unsafe member paths,
OCI whiteout names, sparse files and device entries fail explicitly. It never
extracts package entries onto the build host's filesystem. `Architecture: all`
is accepted for any requested architecture.

`//deb/tests:import_debs_test` tests archive fidelity and rejection behavior;
`//deb/tests:import_rule_test` exercises the public rule and output groups.
Their small package inputs are checked in. To regenerate them, run
`python3 deb/tests/make_fixtures.py` **outside Bazel** and review the binary hashes
in `deb/tests/fixtures/sha256.json`. Neither test produces a DEB. Source CI audits
the selected action graph before running both tests on AMD64 and ARM64.
