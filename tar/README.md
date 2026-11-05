# Deterministic tar packages

Debug symbol archives from `debug_symbols_tar`, `sonic_deploy_tar` and the OCI
debug layer store regular symbol files as `0644`, matching Debian's detached
debug data. The standard `tar.bzl` mtree mutation pipeline changes only file
modes; directory modes, links, contents, timestamps and
ownership retain their existing behavior. `//tar:debug_symbols_ownership_test`
checks the public debug archive, and `//tar:debug_symbols_modes_test` checks
these metadata boundaries on an actual tar archive.

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
`0.10.5-sonic.1`, published through the SONiC registry. The option accepts a
string: choose another fallback or use `""` to retain the original behavior.
It works with inline and file-label manifests; the dependency owns parsing and
regressions for directives, continuations and escaped paths. Paths, payloads,
ownership, modes and explicit timestamps are preserved. No SONiC tar wrapper is
needed. `sonic_deploy_tar(default_mtime = ...)` forwards an override for its
runtime archive; its separate debug archive retains its generated manifest.

A consuming root must select the patched tar module. Upstream `0.10.5` sorts
above `0.10.5-sonic.1`, so the root `MODULE.bazel` also needs
`single_version_override(module_name = "tar.bzl", version = "0.10.5-sonic.1")`
while other dependencies request the upstream release. Overrides declared by
non-root modules do not affect resolution.

`//tests:deploy_tar_timestamps_test` checks actual archives from both the direct
tar and deployment paths, including stripped programs, explicit zero timestamps,
custom fallbacks and debug output. Generic manifest and changed-input-mtime
regressions live with the tar.bzl patch. These targets produce no DEBs.

## Installed ELF and debug files

`sonic_deploy_tar` splits each ELF into a runtime file and matching build-id
symbols. Keep the native binary's build-tree search paths for Bazel consumers;
the installation step edits a writable copy before splitting it.

Set `compress_debug_sections = True` when the original installation compresses
DWARF, such as Debhelper's dbgsym flow. GNU objcopy compresses the extracted
sections before the runtime debuglink is computed. The default `False` retains
the existing uncompressed format. These attributes are also available on
`//binary:strip_binary.bzl`'s `strip_binary` macro.

`//tests:debug_compression_test` checks both compression policies through actual
runtime/debug archives, verifies unchanged DWARF and executable code, checks
build-id/debuglink pairing and executes the installed program.

Set `install_rpath = "/usr/lib/<triplet>/frr"` when the original installation
requires an exact search path. This replaces every existing entry on the
installation ELF before splitting debug symbols and preserves DT_RPATH or
DT_RUNPATH. An empty value keeps the existing default/preservation policy.
The linked build-tree ELF remains unchanged.
