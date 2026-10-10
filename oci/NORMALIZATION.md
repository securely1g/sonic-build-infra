# Normalize payload paths for an OCI base

`normalize_layer` adapts a TAR to the directory aliases in an explicitly checked
base image. For example, Debian packages may contain `lib/example.so`, while a
merged-usr base requires `usr/lib/example.so` and must keep `lib -> usr/lib`:

```starlark
load("@sonic_build_infra//oci:normalize_layer.bzl", "normalize_layer")

normalize_layer(
    name = "runtime_payload",
    src = ":imported_packages",
    base = ":config_engine_base_layout",
    expected_platform = "linux/amd64",
)
```

The default alias policy is `bin -> usr/bin`, `lib -> usr/lib`,
`lib64 -> usr/lib64`, `sbin -> usr/sbin` and `var/run -> /run`. Each source
entry and hardlink path is rewritten to the corresponding destination. Redundant
entries for the aliases themselves are removed only after their expected kinds,
owners and modes match. The rule validates the OCI layout and the actual final
base layers, including whiteout deletions, before assuming those aliases.

Imported payload contents, file modes, UID/GID, owner names, timestamps and
ordinary symlink targets are preserved. Unsafe paths, whiteouts, sparse files,
devices and attempts to replace directory aliases are rejected. No package is
installed on the host.

Source-owned payloads can opt into `root_owned = True`, which clears build-user
ownership (including PAX ownership fields) and sets symlink modes to `0777`.
`modes = {"usr/share/example.json": "0644"}` changes only explicitly listed
installed paths. These source fixes are not implicitly applied to imported DEBs.
Keep original owner targets in `tars = [...]` when their debug providers must
remain reachable by `debug_symbols_layer` through this adapter.

The default output is `<name>.tar`; output group `normalization` supplies
`<name>.normalization.json` with source/output hashes, the checked base manifest
digest, requested transformations and member counts. To use a different base
layout, pass `directory_aliases` as a mapping from alias paths to their literal
symlink targets. Relative symlink targets are resolved relative to the alias's
parent inside the image. An empty mapping disables path aliases while retaining
payload safety checks and optional ownership/mode normalization.

`sonic_oci.normalize_layer`, exported by `//oci:lib`, supplies the same functions
for adapters that additionally record source package metadata. Use
`normalized_member` for individual TAR entries and `base_aliases`/`normalize`
with an explicit `expected_platform`; do not copy the implementation into each
container. The shared source CI runs `//oci:normalize_layer_test` on both native
architectures. Tests create only TAR/OCI fixtures and never DEB packages.
