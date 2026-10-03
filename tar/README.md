# Deterministic tar packages

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
