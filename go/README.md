# Go module ZIP locks

Gazelle's selected module graph determines the versions. `go.mod`, `go.sum` and
`go.work` retain their usual roles. A reviewed JSON ZIP lock maps
`module@version` to the SHA-256 of the proxy archive. Go's `h1:` checksum verifies
module contents; the ZIP digest makes the bytes addressable by Bazel's local
repository cache and remote downloader.

Use `gazelle@0.54.0.sonic.1` and configure the participating extension:

```starlark
go_deps = use_extension("@bazel_gazelle//:extensions.bzl", "go_deps")
go_deps.zip_lock(file = "//:go.zip.sums.json")
use_repo(go_deps, go_zip_lock_plan = "bazel_gazelle_go_repository_config")
```

Start the JSON file with `{}`. A root graph lock covers dependencies contributed
by all modules participating in that extension. Each isolated extension exports
its own plan and needs its own root lock. Independent component roots update
and commit their own locks. Dependency-owned locks are never update destinations.

Invoke this script using Python 3.11 or newer, from the root to be resolved:

```sh
python3 /path/to/sonic-build-infra/go/lock.py \
  --bazel /path/to/bazel \
  --plan '@go_zip_lock_plan//:zip-lock.json'
```

The updater uses the host-compatible Go SDK selected by rules_go, as exported
by Gazelle's plan. It disables automatic Go toolchain switching. Only missing
ZIP digests are downloaded, through `go mod download`, preserving Go's proxy,
authentication and checksum database behavior. The returned `h1:` must match
the resolved graph before any lock is replaced. Existing reviewed digests are
reused. Removed modules are pruned; conflicting or invalid digests fail. All
files are staged after successful resolution and verification. Network and
checksum failures preserve every old lock. `--check` checks exact graph coverage
without updating locks or downloading module ZIPs.

Ordinary builds use one explicit HTTP(S) `GOPROXY` (default:
`https://proxy.golang.org`). They reject proxy lists, `direct` and `off` rather
than silently changing Go's fallback rules. When `GOPRIVATE` or `GONOPROXY` would
bypass the proxy, explicitly configure `GONOPROXY=none` for the managed proxy,
or use an archive override. Proxy credentials use Bazel's netrc support.
`--experimental_remote_downloader` and `--repository_cache` apply to module ZIPs;
module metadata and build actions have separate caching paths.

An explicit `go_repository(version=..., sum=..., zip_sums=...)` exports a plan
in update mode too. Import its repository and pass an additional
`--plan '@repository//:zip-lock.json'`. This declares the update scope without
scanning or interpreting Starlark source. Existing `urls` plus `sha256`
repositories keep their archive interface.

The updater consumes Bazel's structured repository mapping and exported plans;
it does not infer canonical repository names. Bazel 8.5.1 is the buildimage
validation baseline. Pass `--startup-option` and `--bazel-option` repeatedly
when invoking the shared script directly. A Bazel wrapper can also supply these
options consistently across all calls.

Run the updater contract tests with a Go SDK path:

```sh
TEST_GO_SDK=/path/to/sdk python3 go/lock_test.py -v
```
