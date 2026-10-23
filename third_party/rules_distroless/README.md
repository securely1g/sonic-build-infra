# Import an APT lock directly into Distroless

The base and config-engine image work in [Buildimage #30](https://github.com/securely1g/sonic-buildimage/pull/30)
currently repeats the same Debian package versions in `apt.lock.json` and a
676-line generated `apt_inputs.MODULE.bazel`. The lock already records each
archive's URL, SHA256 digest, architecture and dependency graph.

`patches/0001-import-apt-lock.patch` adds a public `apt.import_lock` tag to the
existing `rules_distroless` APT extension. Consumers need only the reviewed lock:

```starlark
apt = use_extension("@rules_distroless//apt:extensions.bzl", "apt")
apt.import_lock(lock = "//dockers/docker-base-trixie/bazel:apt.lock.json")
use_repo(apt, "base_debian", "base_debug_debian", "config_engine_debian")
```

`dependency_sets = ["base_debian"]` can limit the imported sets. Every root and
transitive package has independent `:data` and `:control` targets, for example
`@base_debian//libc6:data`, plus the usual package-closure target. This lets
SONiC's existing package selector choose a package without accidentally copying
all its dependency files over the base image. [Buildimage #13](https://github.com/securely1g/sonic-buildimage/pull/13)
uses the same base-image dependency and can adopt this interface.

The tag reads exact archive identities from the lock without resolving package
indexes or rewriting the input. It reuses Distroless's `deb_import`, `deb_postfix`
and dependency-set generator; it does not introduce a separate DEB extractor or
image assembler. Each set gets separate internal repository names so two locks
can contain different snapshots of the same package/version. Ordinary
`apt.install` continues to provide C++ compiler and header exports; a runtime
lock does not contain the Contents indexes those exports require.

## Patch ownership and distribution

Maintain the patch and its tests here. The registry distributes matching bytes
in `rules_distroless/0.9.4.sonic.11`, after the existing cumulative `.sonic.10`
patches. The patch header records its origin, problem and validation. It is a
SONiC-maintained change, not an imported upstream commit. Upstream's APT
extension was checked at blob `c82b0edb1fa57184c78d4d0ac8967aed00e7cbdf` and had
no locked-import tag.

Keep the normal `sonic-build-infra` toolchain dependencies unchanged: this patch
is consumed through the Distroless registry entry. Do not edit published older
registry versions.

## Validation

The patch includes three Starlark regressions for graph closure (including
cycles), architecture-independent packages, selecting sets, missing dependencies,
wrong identities/architectures, invalid hashes and duplicate versions.

`tests/locked_import` is copied unchanged into the registry module's
`registry_ci/locked_import` overlay. Its integration test imports existing
Debian Lua/netbase archives and their dependencies from the reviewed snapshot.
It checks independent data/control targets, locked versions, architecture,
complete closures, and unchanged file contents, permissions, owners and links
when the same lock is imported twice. It never creates a DEB.

The registry's native AMD64 and ARM64 jobs run these tests plus all retained
Distroless regressions. In a consumer selecting the proposed registry version:

```sh
bazel test \
  @rules_distroless//apt/tests:locked_import/closure \
  @rules_distroless//apt/tests:locked_import/invalid_lock \
  @rules_distroless//apt/tests:locked_import/selection \
  @rules_distroless//registry_ci/locked_import:archive_contract_test
```

Run on the native architecture being reported. Examining ARM64 archives on an
AMD64 host is useful input validation, but does not establish native ARM64
execution. The `Locked APT import (AMD64)` and `(ARM64)` source PR jobs run
`ci/locked-import-ci.py`. They fetch the cumulative `.sonic.10` recipe from canonical registry `main`,
published by [registry #96](https://github.com/securely1g/sonic-bazel-registry/pull/96)
at `e9c04070eadcf08e9d37b3a7e510c5481e99f432`. They verify its recorded
`source.json` hash, including the upstream archive and
every patch/overlay digest, then apply this checkout's patch and copy this
checkout's tests. A private consumer uses that patched module through a local
path override. The normal root toolchain dependency remains unchanged.

The driver requires matching host and Debian userspace architecture and runs all
four targets above. It starts without a resolution lock and retains the generated
`MODULE.bazel.lock`, source hashes, tested checkout, target platform, test results
and logs in one evidence TAR. Source files must remain unchanged. These jobs test
the source-owned Starlark feature and import existing Debian archives; they do
not compile those packages or create DEBs. Registry CI remains separate consumer
validation.

The #96 recipe is published on maintained `main`; no temporary registry endpoint
is required. Dependency readiness is independent of CI completion. Continue
reporting each native source job against its tested head and base, and do not
relabel registry-only success as a passing source job.
