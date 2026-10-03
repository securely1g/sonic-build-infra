# Shared Rust dependencies for SONiC

Common and SWSS use this module for their third-party Rust libraries. For
example, Common's `CxxString` implements Serde's serialization interfaces and
SWSS passes it to `serde_json`. Both now use one compiled Serde library, so
Common does not need public aliases for `serde` and `serde_core`.

`sonic-rust-deps` is a separate Bazel module in this directory. It contains no
SONiC source library and has no dependency on Common or SWSS. Common still owns
its Rust library, C bindings and tests. SWSS depends on that library directly.
The registry publishes this subdirectory from a pinned `sonic-build-infra`
commit. Consumers use public labels such as `@sonic_rust_deps//:serde_json`.

## Pins and features

`Cargo.toml` contains the union of third-party dependencies and requested
features from Common and SWSS, including their tests and Cargo build helpers.
`Cargo.lock` preserves their existing package versions and checksums. The
initial shared lock has 324 crates.io packages; it changes no third-party pin.
SONiC source packages remain in their owning modules instead of this lock.

The shared graph uses Rust 1.90.0 and `rules_rust` 0.74.0 on native AMD64 and
ARM64 Linux. Different target platforms still compile separate artifacts.
Features are combined deliberately. For example, Serde needs Common's `rc`
support, and Tokio includes the features needed by SWSS and both test suites.
SWSS's tracing features also apply to standalone Common builds: debug logging
is compiled in for development, while release builds cap it at info level.
This matches the combined Cargo graph when Common is built inside SWSS, but
differs from Common's previously separate Bazel feature selection.

When adding or updating a dependency:

1. Update the owning component's Cargo manifest and tracked lock as usual.
2. Update this manifest and lock, preserving unrelated package pins. Add a
   public alias in `BUILD.bazel` when the component needs a new direct crate.
3. Prepare Common and SWSS. Preparation rejects differing package pins,
   incompatible declared versions and missing shared features. Stable Cargo
   version requirements are supported; unfamiliar syntax fails explicitly.
4. Run this module's tests and the affected owner and consumer tests on both
   architectures. Keep each component's normal Cargo workflow working too.

## Prepare before building

The upstream Cargo-to-Bazel generator runs during preparation. Its generated
`Cargo.Bazel.lock` and Bazel's `MODULE.bazel.lock` are ignored and retained as CI
evidence. Only the reviewed Cargo manifest and Cargo lock are checked in.

From the `sonic-build-infra` checkout:

```sh
python3 tools/rust/prepare.py --workspace rust/deps \
  --receipt rust/deps/preparation.json
cd rust/deps
bazel test //tests:serde_roundtrip_test //tests:tracing_test
```

Components use their pinned preparation launcher. The shared helper stages a
private, writable copy of the declared registry module, generates its metadata
and validates the component's native Cargo inputs. It writes the required
module override only after all checks pass:

```sh
python3 /verified/prepare.py --workspace /source/component \
  --shared-dependency=sonic-rust-deps=sonic_rust_deps \
  --staging-dir /source/component/.cargo-bazel-prep \
  --overrides-rc /source/component/.bazelrc.rust \
  --receipt /source/component/artifacts/rust-preparation.json
```

`--dependency=sonic-swss-common=sonic_swss_common` also stages Common and checks
its Cargo inputs against the same shared graph. When an image build already
has a verified Common checkout, use its explicit module override and
`--consumer-workspace=/source/common` instead. Component and image Bazel
invocations must import the generated override file.

Registry CI uses `--shared-only` when its temporary consumer has no Cargo
workspace. This skips only the native consumer manifest check; shared lock
generation, source evidence and generated package checks still run.

The receipt's `shared_dependency.workspace` identifies the private module.
Archive its `Cargo.toml`, `Cargo.lock`, `Cargo.Bazel.lock`, `MODULE.bazel.lock`,
`source-resolution.json` and `preparation.json`, together with the root receipt.
Do not archive the entire fetched source or Bazel output directory.

## Tests

The `Shared Rust dependencies` workflow runs preparation tests and native Rust
tests on AMD64 and ARM64. The JSON test checks Serde's derive macros and `Rc`
support together with `serde_json`; the tracing test checks that the logging
library and subscriber share compatible interfaces. Component CI must also
test a real Common value through SWSS's JSON dependency and inspect its resolved
Bazel graph. Passing these standalone smoke tests alone does not prove that
every Common or SWSS target works.
