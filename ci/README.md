# Source pull request CI

`Bazel (AMD64)` and `Bazel (ARM64)` run on native GitHub-hosted Ubuntu
runners in the same pinned Debian Trixie image. PR events cover all branches
and paths; push events also validate `master`. Checkout uses the proposed merge
revision, so the checks exercise the current PR with its target branch.

Run `bash ci/bazel-ci.sh` on a native AMD64 or ARM64 Debian Trixie host with
Bazel 8.5.1, a C/C++ compiler, binutils, GDB, Git, Python, tar and xz installed.
Local builds default to `sonic-bazel-registry/main` and the Bazel Central
Registry. While `rules_distroless` 0.9.4.sonic.1 is unmerged, this Draft's Bazel
and declared-tool workflows select the registry branch
`codex/distroless-dotted-version` through `SONIC_BAZEL_REGISTRY`. This replaces
the SONiC endpoint; it does not add a second fallback to the same registry.
Remove the workflow override after that entry lands. On a native Trixie host,
use the same temporary endpoint with:

```sh
SONIC_BAZEL_REGISTRY=https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/codex/distroless-dotted-version bash ci/bazel-ci.sh
```

CI selects its URLs explicitly so user-specific Bazel settings cannot change
its registries.
The main branch contains the landed tar.bzl patch. The separate shared-Rust
workspace uses its unchanged BCR dependencies. The source
script selects matching execution/target platforms. Module versions, source
checksums and package snapshots remain explicit inputs. The repository deliberately
excludes its large generated module lock; CI retains the resolved lock as evidence.

The explicit source tests cover C/C++ linking, shared-library/PIC behavior,
stripping and deployment debug-provider/content behavior. The protoc smoke test
generates C++, Python and Python type stubs with imported schemas and proto3
optional fields using its declared compiler runtime. The shared API branch
also runs the nine external-consumer analysis/sysroot tests and rejects the
unsafe archive fixture. Wheel installation tests run when the wheel-layer rule
is present; archive ownership and debug-header ownership tests run when the
ownership rule is present. These rule families are selected independently, so
either can be developed and validated on its own. These checks do not claim
ARMHF execution, an installed SONiC image, or full component downstream coverage.

Archive timestamp checks cover the shared default and caller overrides, explicit
zero times, and both direct tar and stripped-binary deployment paths. The tar.bzl
patch owns generic manifest and changed-input-mtime regressions. The source
workflow retains a plain tar and a runtime/debug pair using omitted timestamps
as well as the existing deployment samples. It also records the effective
registry URL and generated module lock for dependency inspection.

Each run retains test XML/logs, build events, native host metadata, revision,
resolved module locks, sample executables, and their runtime/debug deployment
tars. Successful runs include checksums in `provenance.json`. Download the
architecture-specific artifact from the Actions run page. Partial evidence is
retained when a validation step fails.

The installed deployment-tar check verifies the complete runtime file set,
payload ownership and modes, declared data bytes, native ELF architecture,
execution of the extracted program, matching build IDs, debuglink checksum,
and GDB source-line lookup through the extracted debug tree. Its report is
retained as `deploy-archives.json` alongside both archives.

CI selects runtime/debug tar outputs directly. The separate Debian-control
workflow is removed so source PRs do not create fixture DEBs. Legacy Debian
rules and fixtures remain available for explicitly authorized DEB validation;
they are outside the automatic source CI selections.

Both architecture checks must be required on the PR target branch. Repository
settings enforce this separately from the workflow; skipped or missing jobs
are not successful source coverage.
