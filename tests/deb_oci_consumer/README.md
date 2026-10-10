# Existing Debian packages in a real OCI consumer

This standalone module exercises the public `deb_import` output as an actual
`rules_oci` image input. It uses the checked-in sample Debian packages from
`deb/tests/fixtures`; no Bazel target creates a Debian package or runs its
maintainer scripts.

`base_packages` imports the gzip sample. `overlay_packages` imports the XZ,
bzip2, and uncompressed samples in that order. Both default TAR outputs go
directly into `oci_image(tars = ...)`, so the final regular file contains
`raw`. The test checks the OCI index, config, ordered layer descriptors and
hashes, package receipts, file contents, ownership, modes, links, timestamps,
and the extended metadata carried by the samples.

Independent importer actions must produce identical TARs. Two OCI actions
with the same layer targets must produce byte-identical layout files. The
same layer targets are intentional: upstream `rules_oci` records each layer's
owning Bazel label in image history, so differently named input targets carry
different provenance even when their payload bytes match.

The only local override is this infrastructure checkout, the implementation
under test. `rules_oci` 2.2.6 matches the maintained image consumer. Other
dependencies resolve through the same SONiC registry and Bazel Central
Registry as the parent repository.

After inspecting the action graph for package-producing actions, run from
this directory on native AMD64:

```sh
bazel aquery //:oci_consumer_test --output=jsonproto
bazel test //:oci_consumer_test
```

On native ARM64, append `--config=aarch64` to both commands. The test uses
architecture-independent package inputs while checking the selected OCI
platform and running the actual upstream tools natively. It does not claim
container boot or application runtime coverage.
