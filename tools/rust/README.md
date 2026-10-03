# Generate Rust Bazel metadata before building

Keep `Cargo.lock` in Git. Ignore `Cargo.Bazel.lock` and regenerate it before the
first Bazel command that loads Rust repositories. `prepare.py` invokes the pinned
upstream `rules_rust` crate extension using `bazel fetch --repo=@crates`; it does
not implement Rust build rules or compile targets. Fetching the named crate
repository avoids evaluating unrelated extensions, unlike `bazel mod show_repo`. Python 3.11 or newer and the component's pinned Bazel are needed.

The component must use `crate.from_cargo(...,
skip_cargo_lockfile_overwrite = True)` with an explicit `Cargo.lock` and generated
`Cargo.Bazel.lock`. With rules_rust 0.74.0, the skip option makes splicing read the
existing Cargo lock directly instead of running a dependency update.

```sh
python3 /path/to/sonic-build-infra/tools/rust/prepare.py \
  --workspace "$PWD" --repository crates \
  --receipt .cargo-bazel-prep/preparation.json
bazel build //path/to:target
```

`--repository` is the apparent alias in `use_repo`, usually `crates`, even when
the extension's internal repository name is different. Use repeatable
`--bazel-startup-arg=...` and `--bazel-arg=...` for output directories, registry
settings, and module overrides. Both AMD64 and ARM64 dependencies come from the
component's `supported_platform_triples`; preparation runs on the native host.

## Prepare a component used by another Bazel module

rules_rust 0.74.0 requires generated metadata to exist for non-root modules.
Prepare each component as a root first, then supply its writable source directory
as a module override to its consumer. When the consumer already has a recorded
source checkout (for example sonic-buildimage's Common and SWSS submodules), run
this helper on Common first, then SWSS with the Common override, and pass both
source overrides to the image build.

For a standalone consumer that obtains Common from the registry, the helper can
resolve and fetch the module already declared by `MODULE.bazel`, stage a private
writable copy, prepare it, and prepare the consumer:

```sh
python3 /path/to/sonic-build-infra/tools/rust/prepare.py \
  --workspace "$PWD" --repository crates \
  --dependency=sonic-swss-common=sonic_swss_common \
  --staging-dir .cargo-bazel-prep/dependencies \
  --overrides-rc .bazelrc.rust \
  --receipt .cargo-bazel-prep/preparation.json
bazel --bazelrc=.bazelrc.rust build //path/to:target
```

Alternatively add `try-import %workspace%/.bazelrc.rust` to the component's normal
`.bazelrc`. Ignore both `.bazelrc.rust` and `.cargo-bazel-prep/`. The helper removes
only its own prior generated overrides file before resolving dependencies, so an
old prepared Common cannot hide a new module pin. It refuses a symlink or a file
without its generated marker. Dependencies in this convenience mode have root
`Cargo.lock` / `Cargo.Bazel.lock` files and use the apparent `crates` alias.
Other layouts can use explicit component-by-component calls.

Staged dependency preparation uses its own output directory and Bazel batch
mode. This overrides shared output directories set by CI setup actions and
exits the dependency JVM before the consumer build starts. The consumer keeps
its normal Bazel server and cache settings.

Component launchers can fetch this single helper file using a reviewed immutable
sonic-build-infra commit and verify its SHA256 before running it. This preparation
tool pin is independent of the component's selected Bazel build-infra module.

## Validation and retained evidence

Every invocation regenerates metadata, even if a cached generated file exists.
Download caches and Bazel's repository cache can still save transfer time. The
helper requires `Cargo.lock` to remain byte-identical and checks every generated
package against its locked name, version, registry checksum or Git revision.
On failure it removes generated metadata and restores any changed source lock.
It records source-lock and metadata hashes, manifest/module hashes, selected
packages, the helper hash, command and dependency overrides in its receipt.

Retain `Cargo.Bazel.lock`, `MODULE.bazel.lock`, preparation receipts and dependency
receipts as CI artifacts. Generated metadata and receipts are build evidence,
not source inputs to trust on a later run. Component CI must also build and test
its actual Rust targets on native AMD64 and ARM64, and validate downstream users.
Run the helper's focused regression checks without producing packages:

```sh
python3 tools/rust/prepare_test.py
```
