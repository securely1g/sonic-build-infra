# Deterministic tar packages

Use `sonic_tar` for tar.bzl archives with explicit entry lists:

```starlark
load("@sonic_build_infra//tar:sonic_tar.bzl", "sonic_tar")

sonic_tar(
    name = "program_pkg",
    srcs = [":program"],
    mtree = [
        "usr/bin/program type=file mode=0755 content=$(location :program)",
    ],
)
```

The wrapper supplies `time=1672560000` for files and directories that omit it,
matching tar.bzl's automatic manifest. Missing link timestamps become `time=0`.
Explicit timestamps, including `time=0`, remain unchanged. Archive paths, payloads,
ownership, modes and link destinations also remain unchanged. This prevents an
input's build or cache-restoration timestamp from changing package checksums.

`sonic_deploy_tar` applies the same defaults to its `binaries` keys and `mtree`
entries. Components do not need to repeat the timestamp policy.

Supported manifests are `"auto"` or flat lists containing one complete entry per
line. Escape whitespace in paths using mtree syntax, such as `\040`; Bazel
`$(location ...)` expressions remain supported. Blank lines and comments are
preserved. `/set`, `/unset`, `..` navigation, continued lines and label-valued manifests are
deliberately excluded: use upstream `tar` with a manifest whose producer sets
deterministic times for those advanced forms. The wrapper fails on unsupported
forms rather than silently leaving timestamps dependent on inputs.

Common's library, programs and configuration archives use this shared policy;
its package tests remain responsible for checking their installed layout and
expected headers. SWSS's existing explicit `time=0` entries retain their values.

`//tar:reproducible_tar_test` runs the selected real bsdtar against identical
payloads with different filesystem timestamps and requires byte-identical
archives. `//tests:deploy_tar_timestamps_test` checks the deployment path with
stripped binaries and matching debug output. These targets produce no DEBs.
