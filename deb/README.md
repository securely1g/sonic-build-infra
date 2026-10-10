# Import existing Debian packages

`deb_import` accepts existing `.deb` file labels in the caller's installation
order. The declared `tar.bzl` BSD tar toolchain extracts the package members and
composes their filesystem contents into a PAX TAR. Python validates the inputs
and outputs and records package metadata; it does not implement archive parsing,
decompression or TAR generation. For example, a Make prerequisite can supply a
package file while Bazel owns its extraction and every subsequent image action:

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
hardlinks and extended metadata. BSD tar may normalize TAR headers and add
equivalent PAX metadata; the original header bytes are not preserved. The
importer compares every input and output member and fails if required metadata
or contents change. Package order is preserved, including repeated paths across
packages: the later package's entry wins when an image layer is applied.
No source package is built, no ambient host `dpkg`, `ar` or `tar` is called, and
no maintainer script runs. Package files are never installed on the build host.
This is payload import, not full Debian installation. Consumers still own
required installation state, path normalization and image policy.

Pass the default output to `rules_oci`'s `oci_image(tars = [":runtime_packages"])`
to assemble the container. Debian repository dependencies continue to use
`rules_distroless` through the [APT adapter](../apt/README.md). Its pinned public
`flatten` rule writes GNU TAR, which drops PAX extended attributes; local imports
therefore use the upstream BSD tar toolchain's PAX composition directly. No
custom archive writer or OCI converter is introduced.

The `manifest` output group supplies `runtime_packages/manifest.json`:

- `schema: 1`, the requested architecture, caller metadata and ordered `packages`.
- Each package's actual name, version, architecture, complete `control_fields`
  (including `Depends`, `Pre-Depends` and multiline descriptions), and SHA-256
  hashes of all `control_files`.
- `control_sha256` retains the original decompressed control TAR hash, including
  its original headers and padding. `control_output_sha256` identifies the plain
  PAX control TAR produced by BSD tar. These hashes may differ.
- The original `source_deb` filename, declared `source_label`, SHA-256 and size;
  plus the original decompressed data TAR's hash, size and member count.
- The aggregate `payload` filename, SHA-256, size and member count.

Original control/data hashes and sizes are computed from Python's existing
read-only archive stream; that stream is never copied into an archive output.
This preserves reviewed source-package pins while the emitted TARs use upstream
normalization. The aggregate `payload.sha256` always describes the actual output.

The `controls` group exposes plain PAX control TARs produced by BSD tar. They
retain original file contents and metadata, including maintainer scripts for
inspection; importing never executes those scripts.
`DebImportInfo` exposes the same `payload`, `manifest` and `controls` files to
other Starlark rules. Caller metadata cannot replace verified reserved fields.
`required_packages` in metadata is checked against extracted package names.

Pass a parent's manifest as `runtime_manifest` for a debug import. The importer
records that file's SHA-256 and rejects a repeated package if its original DEB
bytes differ. This supports downstream APT selection using the precise retained
package controls, without treating caller context as producer provenance.

The import and read-only validation path supports Debian 2.0 archives containing
uncompressed, gzip, xz or bzip2 control/data TARs. The pinned Python validation
runtime does not support zstd. Unsupported compression, malformed
archives, duplicate package names, foreign architectures, unsafe member paths,
OCI whiteout names, sparse files and device entries fail explicitly. It never
extracts package entries onto the build host's filesystem. `Architecture: all`
is accepted for any requested architecture.

`//deb/tests:import_debs_test` tests metadata fidelity and rejection behavior;
`//deb/tests:import_rule_test` exercises the public rule and output groups.
Their small package inputs are checked in. To regenerate them, run
`python3 deb/tests/make_fixtures.py` **outside Bazel** and review the binary hashes
in `deb/tests/fixtures/sha256.json`. Neither test produces a DEB. Source CI audits
the selected action graph before running both tests on AMD64 and ARM64.

`tests/deb_oci_consumer` tests this checkout through its public API with
`rules_oci`. CI builds actual OCI layouts and checks descriptors, ordered layers,
final filesystem contents and reproducibility on both native architectures.
