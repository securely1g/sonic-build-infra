# Shared Rust dependencies for SONiC

Common and SWSS depend directly on this module's third-party Rust targets.
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
features from Common and SWSS, including their tests and Cargo build helpers.
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
