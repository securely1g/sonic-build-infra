# Prepare a source repository

Use `http_archive` for one fixed archive and ordinary patches. Use
`prepared_source` when the source recipe requires an ordered chain of archive
merges, patch stages and nested extraction. Compilation still uses native
language rules against the prepared files.

`prepared_source` calls only Bazel repository builtins. `steps` is an ordered
list of JSON strings with four fixed operation schemas; it cannot
execute commands or evaluate expressions. Version, URLs, checksums, paths,
patch order and BUILD declarations belong to the caller.

```starlark
prepared_source = use_repo_rule(
    "@sonic_build_infra//source:prepared_source.bzl",
    "prepared_source",
)
prepared_source(
    name = "prepared_component",
    build_file = "//:component.BUILD.bazel",
    inputs = {
        "local/fix.patch": "//patches:fix.patch",
        "local/series": "//patches:series",
        "local/sub/fix.patch": "//patches/sub:fix.patch",
    },
    steps = [
        '''{
            "op": "archive",
            "urls": ["https://example.org/component-1.0.tar.gz"],
            "sha256": "<64 hexadecimal digits from the fixed input>",
            "strip_prefix": "component-1.0"
        }''',
        '''{
            "op": "archive",
            "urls": ["https://example.org/component-1.0.debian.tar.xz"],
            "sha256": "<64 hexadecimal digits from the fixed input>"
        }''',
        '{"op": "patch", "input": "local/fix.patch", "strip": 1, "format": "unified"}',
        '{"op": "patch_series", "series": "debian/patches/series", "format": "unified"}',
        '''{
            "op": "patch_series",
            "series_input": "local/series",
            "patch_inputs": ["local/sub/fix.patch"]
        }''',
        '''{
            "op": "extract",
            "archive": "vendor/dependency.tar.gz",
            "output": "vendor",
            "strip_prefix": "dependency-2.0"
        }''',
    ],
)
```

In `MODULE.bazel`, use JSON string literals as above: that file has no `json`
global. A recipe defined in a `.bzl` file can use `json.encode` to produce the
same strings.

The example requires real fixed inputs. Its local `series` contains
`sub/fix.patch`. `inputs` maps logical relative paths to labels, retaining Bazel's
file-change tracking; those files are read directly, without staging a copy.

| Operation | Required keys besides `op` | Optional keys |
| --- | --- | --- |
| `archive` | `urls`: nonempty URL list; `sha256`: fixed hex digest | `strip_prefix`, `output` (default repository root) |
| `patch` | `input`: a key in `inputs` | `strip` (default 1), `format` (default `native`) |
| `patch_series`, repository file | `series`: repository-relative series path | `strip` (default 1), `format` (default `native`) |
| `patch_series`, label input | `series_input`: key in `inputs`; `patch_inputs`: ordered input keys | `strip` (default 1), `format` (default `native`) |
| `extract` | `archive`: repository-relative archive path | `strip_prefix`, `output` (default repository root) |

Each archive merges into `output` when that step runs. A later archive replaces
an earlier file at the same path, matching `repository_ctx.download_and_extract`
and `extract`. After the final step, the declared `build_file` replaces the
prepared repository's root `BUILD.bazel`.

Patch steps and series default to `format = "native"`: the original patch is
passed directly to `repository_ctx.patch`. Select `format = "unified"` explicitly
for ordinary unified text diffs with adjacent file sections that the native
engine cannot separate. This mode identifies `---`/`+++` file boundaries only
after counting each `@@` hunk's old and new payload lines; a payload that resembles
a file header stays within its hunk. Every file section is still applied by
`repository_ctx.patch`, which owns context matching, offsets, creation and
deletion. The adapter retains native engine behavior and limits. It does not
implement another patch engine or retry a failed native patch automatically.

The original label or archive-contained patch stays unchanged. Temporary file
sections use the reserved `.prepared_source_unified_patch` directory inside the
repository and are removed after successful application. An existing source
entry at that path fails rather than being overwritten. Incorrect or truncated
hunk counts fail. Use `native` for Git metadata only after verifying that the
selected Bazel version supports the required behavior. Unified mode rejects Git
metadata and binary patches. Binary patches require separately verified,
declared tooling; `native` is not a general binary-patch engine. Per-step `strip`
applies unchanged in both modes.

A series allows blank lines and whole-line `#` comments. Each remaining line
must be one relative patch path. Per-patch arguments and inline comments are
rejected. Paths are relative to the series directory. Duplicate paths are
rejected. For label inputs, the complete logical paths and their order must
exactly match `patch_inputs`; patches with the same basename in different
subdirectories remain distinct. Every referenced label patch must be declared
in `inputs`. Repository series patches come from the fixed archive itself.

Unknown operations or keys, absent inputs, unsafe relative paths, checksum
mismatches, missing files and patch failures fail repository preparation. Paths
must not be absolute or contain empty, `.` or `..` components; empty `output`
and `strip_prefix` select their documented defaults. Patch strip is a
non-negative integer. A caller that requires another source-preparation
operation should first establish that concrete need rather than embedding a
command interpreter in this rule.

## Contract tests

From the infra workspace:

```sh
python3 source/tests/prepared_source_test.py --bazel /path/to/bazel-8.5.1 -v
```

The test starts real Bazel repository queries in independent temporary
workspaces. Local fixture archives and patches define expected bytes directly.
It covers ordered archive merging, patch stages, same-basename paths, nested
extraction, label and BUILD changes in the same output directory, and explicit
failure/recovery for invalid checksums, inputs, paths and series. Additional
unified-diff fixtures check adjacent files, header-like hunk payload, multiple
hunks and omitted counts, `/dev/null` creation/deletion, unchanged original patch
inputs, label-change invalidation, scratch collisions, incorrect context and
hunk counts, and rejected Git metadata. It needs no compiler, remote dependency
downloads or host `patch` executable.
