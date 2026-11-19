# Deterministic tar packages

`debug_symbols_tar` builds an installation mtree from the original symbol files
and trees, then invokes `tar.bzl` once to produce an uncompressed `.tar` in every
compilation mode. Both component packages and OCI debug layers use this path.
The manifest maps build-id paths directly to their original file contents;
there is no collected symbol directory or intermediate archive.

The archive retains `/usr/lib/debug/.build-id/NN/REST.debug`, root ownership with
empty user/group names, `0644` regular files, `0755` directories, deterministic
timestamps, and declared installation symlinks. Conflicting symbol destinations
and installation links that replace an existing entry are rejected.
`//tar:debug_symbols_ownership_test` checks the public archive with ordinary
files and generated symbol trees. `//tar:debug_symbols_manifest_test` checks the
rejected destinations. `root_owned_tar` remains available for other archives.

`sonic_deploy_tar` uses the standard `tar()` rule with
`default_mtime = "1672560000"`, matching tar.bzl's automatic manifests. This
fallback prevents the filesystem timestamps of rebuilt or restored files from
changing package checksums. Explicit entry timestamps and inherited `/set`
timestamps remain unchanged. Write `time=0` explicitly on links when their
existing zero timestamp must be preserved.

Direct tar callers use the same option:

```starlark
load("@tar.bzl", "tar")

tar(
    name = "program_pkg",
    srcs = [":program"],
    default_mtime = "1672560000",
    mtree = [
        "usr/bin/program type=file mode=0755 content=$(location :program)",
    ],
)
```

Timestamp handling belongs to the patched `tar.bzl` module
`0.10.5.sonic.2`, published through the SONiC registry. The option accepts a
string: choose another fallback or use `""` to retain the original behavior.
It works with inline and file-label manifests; the dependency owns parsing and
regressions for directives, continuations and escaped paths. Paths, payloads,
ownership, modes and explicit timestamps are preserved. No SONiC tar wrapper is
needed. `sonic_deploy_tar(default_mtime = ...)` forwards an override for its
runtime archive; its separate debug archive retains its generated manifest.

Consumers can request the timestamp support with
`bazel_dep(name = "tar.bzl", version = "0.10.5.sonic.2")`.

`//tests:deploy_tar_timestamps_test` checks actual archives from both the direct
tar and deployment paths, including stripped programs, explicit zero timestamps,
custom fallbacks and debug output. Generic manifest and changed-input-mtime
regressions live with the tar.bzl patch. These targets produce no DEBs.

## Installed ELF and debug files

`sonic_deploy_tar` splits each ELF into a runtime file and matching build-id
symbols. Keep the native binary's build-tree search paths for Bazel consumers;
the installation step edits a writable copy before splitting it.

Extracted DWARF is compressed by default, matching Debhelper's dbgsym
packages. GNU objcopy compresses the extracted sections before the runtime
debuglink is computed. Set `compress_debug_sections = False` when the original
installation ships uncompressed debug sections. These attributes are also
available on `//binary:strip_binary.bzl`'s `strip_binary` macro.

`//tests:debug_compression_test` checks both compression policies through actual
runtime/debug archives, verifies unchanged DWARF and executable code, checks
build-id/debuglink pairing and executes the installed program.

Set `install_rpath = "/usr/lib/<triplet>/frr"` when the original installation
requires an exact search path. This replaces every existing entry on the
installation ELF before splitting debug symbols and preserves DT_RPATH or
DT_RUNPATH. An empty value keeps the existing default/preservation policy.
The linked build-tree ELF remains unchanged.
