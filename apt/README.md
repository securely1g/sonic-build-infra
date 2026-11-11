# One APT source configuration, lock and package collections

`apt/sources.json` is the editable source baseline. `apt/requests.json` owns the
package requests for all collections. `apt/apt.lock.json` is the **single**
reviewed production graph, shared by Docker/rootfs payloads, target development
libraries and execution tools. A package identity is stored once; dependency
sets select their required packages and architecture contexts.

Registered libnl/libyang consumers retain their historical
`//toolchains/bookworm:apt.bzl` namespace. Its library views import this same
production lock, including CMocka, PCRE2 and xxHash headers and libraries;
normal builds do not restart the old Bookworm index resolver.

The Sysmgr runtime, debug and test collections preserve their reviewed AMD64
package selections. `syncd_vs_vim_debug_debian` preserves the Syncd debug
consumer that selects `vim`, while `syncd_vs_debug_debian` continues to select
`vim-nox`. These are separate root selections over one package graph, so adopting
the shared lock does not silently replace one editor package with the other.

The FRR image collections retain the AMD64 runtime libraries and utilities,
debug tools and external OpenSSH library closure requested by
`sonic-buildimage/dockers/docker-fpm-frr`. `frr_image_test_debian` supplies only
the private Redis fixture used by installed-image checks. FRR, libFIB and the
OpenSSH client continue to come from their source-owned installation TARs.

## Update and check

Use the fixed Bazel and Distroless versions selected by the owning workspace.
The shared updater invokes the Distroless resolver and its `export_lock` target
in an isolated preparation workspace; it does not implement another Debian
resolver. Existing package identities, URLs and hashes remain fixed. Reviewed
extracted data/control hashes are preserved when the source DEB is unchanged.

Bookworm's compiler/sysroot, matching libc debug symbols and Perl/instrumentation
collections share this same lock. During an explicit update, collections whose
requested suites overlap are resolved together. Disjoint Bookworm and Trixie
suite families use separate preparation workspaces because the upstream live
index keys omit the suite. This prevents equal package versions in different
distributions from overwriting each other's source or file map. The existing
merge validates their suite-qualified identities and produces one production
lock. Ordinary imports need no partitioning or package-index access.

From the infra source directory:

```sh
python3 apt/lock.py update --bazel /path/to/bazel-8.5.1
python3 apt/lock.py check
```

For a local, reviewed registry candidate use repeated `--registry` arguments
(the first registry wins). `--bazel-startup-option` and `--bazel-option` forward
explicit settings to build, cquery and info; pass values containing a leading
`--` using `=`. For example:

```sh
python3 apt/lock.py update \
  --bazel /path/to/bazel-8.5.1 \
  --registry file:///path/to/sonic-bazel-registry \
  --registry https://bcr.bazel.build/ \
  --bazel-startup-option=--batch \
  --bazel-startup-option=--output_base=/tmp/apt-lock-output \
  --work-dir /tmp/apt-lock-prepare
```

The preparation directory retains each partition's generated MODULE, resolver inputs and
`resolve.log`. A failed resolution or validation leaves the production lock
unchanged. The result is validated before an atomic replacement of the lock.
`check` reads the current source/request documents and validates their recorded
input digest, the selected resolver version, package identities and dependency
closure. It never resolves, downloads or rewrites the lock.

An intentionally authorized source change uses `--allow-source-change` after
editing the single source configuration. An intentionally authorized change of
existing package versions uses `--allow-version-change`. These switches do not
silently permit changing the checksum of an existing package identity. Inspect
and review all such changes. `--resolved-lock` accepts a retained output of the
selected resolver for offline validation; `merge` only migrates reviewed old
graphs and must be followed by a real `update` before the result passes `check`.

The lock records `_inputs.source_document`, `_inputs.requests_document`,
resolver identity, normalized module requests and an input digest. Package
records include exact DEB SHA256 and dependency edges. `files` records the file
mapping needed by library export; resolver index identities remain in `facts`.
Package dependency metadata, request changes and source changes are tracked even
when package versions remain unchanged. Existing pinned vim/make archive hashes
are also checked through the request document's `pinned_packages` records.
The debug collections explicitly select the previously resolved `vim-nox` identity
so a separate pinned `vim` tool cannot change their alternative selection.
Updating collections preserves existing unrelated package records. Image collections
keep their original amd64/arm64 request contexts even though the shared source
also supplies the existing armhf compilation/sysroot collections.

## Ordinary consumption

Normal builds import the lock, validate its source/request binding and select
collections. They do not refresh the source indexes or choose new versions.
For example, root MODULE code can select a payload collection:

```starlark
apt = use_extension("@rules_distroless//apt:extensions.bzl", "apt")
apt.import_lock(
    lock = "@sonic_build_infra//apt:apt.lock.json",
    sources = "@sonic_build_infra//apt:sources.json",
    requests = "@sonic_build_infra//apt:requests.json",
    dependency_sets = ["orchagent_debian", "orchagent_debug_debian"],
)
use_repo(apt, "orchagent_debian", "orchagent_debug_debian")
```

Library consumers use the same lock with `libraries = True`. That keeps the
original Distroless CcInfo/export implementation and supplies locked filemaps;
consumers reference the exported library in `deps`. Execution tools use the
same package graph in the execution configuration and carry their loader,
interpreters, runtime libraries, templates and child tools in runfiles.

`apt_inputs` reads the same lock and generates public package label mappings.
It never resolves or downloads packages and is not another version list.
`apt_layer` handles selection against an OCI base and uses public `data/control`
archives. It assembles selected archives with standard
`flatten(tars=[...], deduplicate=True)`: shared paths appear once and later
non-ELF overrides retain their final bytes and metadata. Rootfs consumers use the same public archives through their own
installation/assembly interface. Neither copying archives nor library export
runs Debian maintainer scripts. Package dependencies, install behavior, accounts,
service setup and status generation must follow the consuming image contract.

### Select one locked root subset

`apt_inputs` also accepts `dependency_set`, `architecture`, and `root_packages`
together. This mode validates the complete selected set/architecture, then
follows locked dependency keys from the requested package names. A requested
name may already be transitive in the shared set. Omitting all three attributes
preserves the existing full-set mapping.

The subset repository emits a restricted `APT_INPUTS` mapping and
`apt_manifest.json`. The manifest contains exactly `schema = 1`,
`dependency_set`, `architecture`, `root_packages`, and `packages`. Its package
object is keyed by full version-2 lock keys; each row contains `name`, `version`,
`architecture`, ordered `urls`, `sha256`, and the locked `depends_on` keys.
Duplicate selected archive hashes fail so a consumer can bind files to rows by
SHA256 without relying on their filenames.

For example, `sonic-build-infra` uses this mode for
`//tools/build_tools:installer_worker_debs_amd64`. The target selects the 39
Debian names requested by `sonic-buildimage`'s installer worker plus Docker CE's
`nftables` dependency, then consumes public `<package>/amd64:deb` aliases.
This is a subset of the existing `build_tools` lock, with no additional APT set
or resolver. See the [worker target contract](../tools/build_tools/README.md#locked-installer-worker-packages).

## Validation

`apt/tests/lock_test.py` checks source/request invalidation, graph completeness,
merge identity/hashes, version preservation, existing extracted metadata,
explicit source-change authorization, and failed-update file protection.
Actual resolver/export and Docker/rootfs/library/tool consumers are separate
integration boundaries and must be verified against the selected candidate.

`//apt/tests/integration:overlap_test` checks unique archive paths and final
payload bytes, permissions, ownership, timestamps and link targets when
selected APT archives overlap.
