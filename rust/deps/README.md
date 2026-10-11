# Shared Rust dependencies for SONiC

Common, SWSS and the Rust supervisor listener depend directly on this module's third-party Rust targets.
For example, Common's `CxxString` implements Serde's serialization traits and
SWSS passes it to `serde_json`. Both use the same `:serde` and `:serde_core`
targets, so Common does not need to expose aliases for those dependencies.

`sonic-rust-deps` is a separate Bazel module in this directory. It contains no
SONiC source library and has no dependency on Common or SWSS. Common still owns
its Rust library, C bindings and tests. SWSS depends on that library directly.
The registry publishes this directory from a pinned `sonic-build-infra` commit.
Consumers use public labels such as `@sonic_rust_deps//:serde_json`, including
when those consumers are themselves dependencies of an image build.

## Pins and features

`Cargo.toml` contains the union of third-party dependencies and requested
features from Common, SWSS and the Rust supervisor listener, including their tests and Cargo build helpers.
`Cargo.lock` preserves their existing package versions and checksums; the
324 crates.io packages keep their pins. SONiC source packages remain in their
owning modules instead of this lock.

The graph uses `rules_rs` 0.1.0 and provides a pinned Rust 1.90.0 toolchain for
standalone and external native AMD64 and ARM64 builds. A consuming root can
select its own toolchain for all imported targets.
Different platforms or configurations may compile separate artifacts.

Features are combined deliberately. Serde 1.0.228 includes Common's `rc`
support, derive macros, and SWSS's `alloc` support. Its public `serde_core`
target is also 1.0.228 and comes from the same graph. Tokio includes the
features needed by SWSS and both test suites. SWSS's tracing features also
apply to standalone Common builds: debug logging is compiled in for development,
while release builds cap it at info level. This matches the combined Cargo graph
when Common is built inside SWSS.

When adding or updating a dependency:

1. Update the owning component's Cargo manifest and tracked lock as usual.
2. Update this manifest and lock, preserving unrelated package pins. Add a
   public alias in `BUILD.bazel` when the component needs a new direct crate.
3. Check that the component and shared locks agree on the selected package
   versions, source checksums and required features.
4. Run this module's tests and the affected owner and consumer tests on both
   architectures. Keep each component's normal Cargo workflow working too.

## Build and test

`rules_rs` reads the tracked `Cargo.toml` and `Cargo.lock` directly when Bazel
resolves the graph, including when this module is a dependency. No generated
`Cargo.Bazel.lock`, preparation launcher or writable module override is needed.
Bazel's generated `MODULE.bazel.lock` is ignored and retained as CI evidence.

From the `sonic-build-infra` checkout:

```sh
cd rust/deps
bazel test --lockfile_mode=update //tests:serde_roundtrip_test //tests:tracing_test
```

The `Shared Rust dependencies` workflow runs native tests on AMD64 and ARM64.
The JSON test checks Serde's derive macros and `Rc` support with `serde_json`,
and checks that those implementations satisfy the public `serde_core` traits.
The tracing test checks that the logging library and subscriber share compatible
interfaces. CI retains the source commit, Cargo inputs, generated Bazel lock,
Serde dependency graph, test logs and results.

Component CI must also test a real Common value through SWSS's JSON dependency
and inspect the resolved Bazel graph. These standalone smoke tests do not prove
that every Common or SWSS target works. Private compiler or binding-generator
libraries can have separate dependency graphs; application crates exposing
Rust traits across repository boundaries must share their defining targets.

## Supervisor listener

Version 0.0.3 adds the listener's Mio polling/signal/process features and public
aliases for its runtime and test dependencies. The only new locked packages
are nix 0.27.1, syslog-tracing 0.3.1 and injectorpp 0.4.0 at Git revision
`c7317906a1d514ef99bf2fbd4c943908c265d280`. Existing package versions and
checksums are unchanged. The listener's native Make Cargo files stay unchanged;
its Bazel build uses this shared lock, and its README records the compatible
version differences explicitly.

The native supervisor I/O test verifies readiness on an isolated pipe, signal
zero against the test's own process, function replacement/restoration through
the pinned injector, and lazy syslog writer initialization. CI runs it on
AMD64 and ARM64 alongside the existing public Serde/tracing tests.

## Exact native library names and release profiles

Version 0.0.4 carries an ELF linking fix for the `rules_rust` version selected
by `rules_rs` 0.1.0. CcInfo can supply a versioned shared library such as
`libexample.so.7` or an artifact without the `lib` prefix. The adapter previously
converted these filenames into unversioned `-l` names that may not exist. The
patch passes the declared artifact path to the native linker after the Rust
archives, preserving ELF SONAMEs and handling duplicate interface filenames.
This also retains DSOs needed by transitive Rust code when `--as-needed` is active. Darwin and Windows retain their platform-specific
handling. The patch header records the pinned upstream revision and reason.
It is applied through `rules_rs`'s existing public `rules_rust.patch` extension.

This permits the standard rustc link path to select the panic runtime and
perform cross-crate LTO. A release executable can use:

```starlark
rust_binary(
    # Other source and dependency attributes are unchanged.
    experimental_use_cc_common_link = 0,
    rustc_flags = ["-Cpanic=abort"],
)
```

The consuming root exposes `rules_rust` through the existing `rules_rs`
extension and explicitly selects `-c opt`,
`--@rules_rust//rust/settings:lto=fat` and
`--@rules_rust//rust/settings:codegen_units=1` when matching that Cargo profile.
Tests that need unwinding omit the abort option. This module does not select a
consumer's profile, change Cargo inputs, or add custom allocator providers.

`//tests:panic_runtime_test` links Rust executables through a separate Rust
library to two real C++ shared libraries: one versioned and one without a `lib`
prefix, both with same-basename interface artifacts in separate directories. It verifies their actual function calls, `SIGABRT` without destructors,
and normal unwinding with a destructor. CI repeats it in normal and optimized
full-LTO profiles on native AMD64 and ARM64. The full-LTO build uses ordinary
rustc linking and bitcode dependencies, rather than the C++ adapter's object-only
compilation path. Installed component and container validation remain with the
source and image owners.
