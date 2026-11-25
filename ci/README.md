# Source pull request CI

`Bazel (AMD64)` and `Bazel (ARM64)` run on native GitHub-hosted Ubuntu
runners in the same pinned Debian Trixie image. PR events cover all branches
and paths; push events also validate `master`. Checkout uses the proposed merge
revision, so the checks exercise the current PR with its target branch.

Run `bash ci/bazel-ci.sh` on a native AMD64 or ARM64 Debian Trixie host with
Bazel 8.5.1, a C/C++ compiler, binutils, GDB, Git, Python, tar and xz installed.
Local builds and both native CI workflows use `sonic-bazel-registry/main`
and the Bazel Central Registry. The checked-in module versions and canonical
APT lock preserve package checksums and native header/library exports.
Automatic PR and `master` checks select the maintained `tar-runtime` suite.
Run that same source suite locally with:

```sh
bash ci/bazel-ci.sh
```

The script ignores user/workspace rc files and selects exactly one SONiC endpoint
plus BCR. It checks the committed APT lock before building, generates
`MODULE.bazel.lock`, and records the resolved module graph and endpoint with the
CI evidence; the generated module lock remains ignored.

The GitHub-hosted AMD64 and ARM64 source workflows download and verify the
[registry's public downloader configuration](https://github.com/sonic-net/sonic-bazel-registry/blob/55c38af2284155eee8c7bf2481e719ec23fa2695/ci/source_mirrors.cfg).
Both source workflows pass its absolute path to Bazel, including sysroot
validation, the nested shared-API workspace and the fresh-output cache check.
CI artifacts record the public file, its URL and its SHA256. Local source checks
default to the checked-in `ci/source_mirrors.cfg`.

Distroless patches, upstream regressions and package-import consumer tests live
with the [registry module](https://github.com/sonic-net/sonic-bazel-registry/tree/main/modules/rules_distroless).
This repository tests its own SONiC rules, toolchains and APT adapters against
the selected dependency recipe; it does not maintain a second Distroless
patch archive or test suite.

The separate shared-Rust workspace uses its unchanged BCR dependencies. The
source script selects matching execution/target platforms. Module versions, source
checksums and package snapshots remain explicit inputs. The repository deliberately
excludes its large generated module lock; CI retains the resolved lock as evidence.

The explicit source tests cover C/C++ linking, shared-library/PIC behavior,
stripping and deployment debug-provider/content behavior. The protoc smoke test
generates C++, Python and Python type stubs with imported schemas and proto3
optional fields using its declared compiler runtime. The source suite
also runs the nine external-consumer analysis/sysroot tests and rejects the
unsafe archive fixture. Wheel installation tests run when the wheel-layer rule
is present; archive ownership and debug-header ownership tests run when the
ownership rule is present. These rule families are selected independently, so
either can be developed and validated on its own. The native matrix also selects
the three existing Bookworm contracts:
`//cc/tests/header_includes:headers_link_test` builds and inspects an executable
and shared library using `@bookworm_build_tools//libperl-dev:libperl_cc`;
`//toolchains/bookworm/tests:abi_test` checks the target ELF ABI; and
`:native_test` executes it with the declared Bookworm runtime. They keep Trixie
execution tools and select the matching Bookworm target architecture, retaining
a separate action audit, test events and logs. These checks do not claim
ARMHF execution, an installed SONiC image, or full component downstream coverage.

The native source matrix explicitly runs runtime-context and native-runtime
contracts, including per-object ORIGIN, RPATH inheritance and RUNPATH isolation,
checker argument forwarding, installation RPATH preservation and GCC prefix maps.
The declared-tool matrix also runs the Trixie test runtime, Bookworm Perl and
Bookworm Valgrind contracts on both native architectures, including changes
limited to `apt/**` or `toolchains/bookworm/**`. Source and declared-tool tests
run with cached test results disabled. The source matrix explicitly runs
`//apt:bookworm_lock_test` against the committed lock to check the three Bookworm
dependency sets on both architectures, including package closures and native
header/library exports.

Archive timestamp checks cover the shared default and caller overrides, explicit
zero times, and both direct tar and stripped-binary deployment paths. The tar.bzl
patch owns generic manifest and changed-input-mtime regressions. The source
workflow retains a plain tar and a runtime/debug pair using omitted timestamps
as well as the existing deployment samples. It also records the effective
registry URL and generated module lock for dependency inspection.

Both architectures prepare the declared kernel build runtime and check its
archive boundaries, compiler, Python packaging modules and Debian shared-library
dependency calculation. The runtime check relocates the declared tools and
uses their private loaders, interpreters and data without `chroot` or device
creation. It compiles and runs native C/C++ host programs, checks the ordinary
deployment interpreter, and, on ARM64, compiles an ARM32 compatibility
vDSO probe. These checks create no DEB packages or kernel image.

Each run publishes an explicit allowlist of test XML/logs, typed action/cache
receipts, native host metadata, revision,
resolved module locks, the APT lock and its source/request inputs, sample
executables, and their runtime/debug deployment tars. Successful runs include
checksums in `provenance.json`. Download the architecture-specific artifact from the Actions run page. Partial evidence is
retained when a validation step fails. Raw Bazel build events, action graphs and
execution logs are private job diagnostics: they may contain process environment
or command-line data and are never uploaded. General, declared-tool and Rust
raw records live under the job's temporary directory; sysroot diagnostic graphs
remain in the private staging directory and are excluded by the same explicit
upload allowlist. The cross-runner artifact contains only its manifest, selected
programs and declared runtime. `ci/tests/evidence_test.py` exercises the actual
workflow allowlists with synthetic command/environment secrets, while retaining
the typed cache proof, test results and runtime outputs needed for review.

The installed deployment-tar check verifies the complete runtime file set,
payload ownership and modes, declared data bytes, native ELF architecture,
execution of the extracted program, matching build IDs, debuglink checksum,
and GDB source-line lookup through the extracted debug tree. Its report is
retained as `deploy-archives.json` alongside both archives.

CI selects runtime/debug TAR outputs directly. `ci/suites.sh` shares the suite
selection between the source and declared-tool workflows. Their manual dispatch
input `suite` accepts `tar-runtime` (the default) or `deb-fixtures`; unknown values
fail before Bazel runs. The existing check names, AMD64/ARM64 matrices, cross
execution and cross-host comparison gates are unchanged. `suite.json` and the
job summary state which scope was selected. A passing runtime suite does not
claim that generated-package fixtures passed.

The runtime suite uses the existing `kernel_runtime_tools_test`, preserving its
compiler, interpreter, packaging-module and shared-library dependency checks.
The declared-tool workflow also reruns that test from a fresh Bazel output root
and requires both prepared runtime kinds to be disk-cache hits. Reading original
DEBs, testing control metadata, and compiling the AMD64-to-ARM64/ARMHF probes
remain in this suite; none generates a DEB.

The explicit `deb-fixtures` suite retains the runtime coverage and adds package
generation: the source workflow selects `hello_deb_optional_fields_build_test`
(four optional-field fixtures), and both workflows use the full
`kernel_runtime_test` (`kernel-tools-check` and its debug package). The declared
tool workflow preserves that full test in both initial and restored-cache runs.
These existing tests and their assertions are unchanged. Request this scope
explicitly when DEB generation is authorized, either through the corresponding
workflow's manual `suite` input or locally with:

```sh
SONIC_BAZEL_CI_SUITE=deb-fixtures bash ci/bazel-ci.sh
```

The configured-action audits still check the entire selected runtime graph,
including the P4 owner tools. Only the explicitly requested optional-field DEB
fixture is outside the ordinary no-DEB graph audit. The kernel package test
creates its DEBs inside the test process, so the explicit suite selection also
gates that behavior; an action-output audit alone cannot detect it.

Both architecture checks must be required on the PR target branch. Repository
settings enforce this separately from the workflow; skipped or missing jobs
are not successful source coverage.
