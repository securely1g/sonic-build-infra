# OCI image helpers

## Add service accounts

`account_layer` is a compatibility name for the public Distroless `accounts`
rule, selected through `rules_distroless 0.9.4.sonic.15`. It preserves
`/etc/passwd` and `/etc/group` from the checked OCI base and appends only the
declared service accounts. For example, the base image adds Redis, and Syncd adds
the `_ssh` group without copying Debian's account list. Distroless owns account
rendering, base validation, merging and the resulting TAR and receipt.

```starlark
load("@sonic_build_infra//oci:accounts.bzl", "account_layer")

account_layer(
    name = "accounts",
    base = ":base",
    architecture = "amd64",
    users = [{"username": "redis", "password": "x", "uid": 100, "gid": 101,
              "home": "/var/lib/redis", "shell": "/usr/sbin/nologin"}],
    groups = [{"name": "redis", "password": "x", "gid": 101}],
)
```

Add `:accounts` once to `oci_image.tars`. `:accounts.receipt` records the checked
base, additions and output hashes. Native AMD64 and ARM64 are supported.
Names and numeric IDs must not conflict with inherited accounts; an identical
existing account is retained. New users must have a primary group, and explicit
group members must exist. Inherited account files must be regular root-owned
files with mode `0644`. Whiteouts are honored, so removed files are not recovered
from an older layer. Existing lines remain byte-for-byte unchanged.

This creates no Debian packages, runs no installation scripts and does not modify
the base image or the build host's accounts. It updates only passwd/group;
password databases and home-directory creation are separate installation tasks.

New consumers can load `accounts` directly from
`@rules_distroless//distroless:defs.bzl` with the same arguments and outputs.
The infrastructure test builds the compatibility name and inspects its TAR and
receipt. Generic account validation and OCI-layer tests live with Distroless;
this repository does not carry another account merger or its test suite.
## Record installed package identities

The `//oci:package_metadata` library owns the OCI layout and selected-file
readers used by the package inventory. They validate package inputs without
depending on the account implementation.

`installed_packages` builds dpkg status and a package receipt from the checked
base, selected Debian controls and source-owned runtime TARs. Source repositories generate their public `runtime_packages.json` target from
existing `debian/control`, `debian/changelog` and the runtime TARs. Producers
declare their required package paths; images add only their own integration checks. Changing a version in the producer's changelog changes the
image inventory without editing another copy of the package controls.

```starlark
load("@sonic_build_infra//oci:runtime_packages.bzl", "runtime_packages")

runtime_packages(
    name = "runtime_packages.json",
    control = "debian/control",
    changelog = "debian/changelog",
    packages = {"example": "//dist:runtime"},
    required_paths = {"example": ["usr/bin/example"]},
)
```

The BUILD mapping selects the Debian package stanza and its public TAR. Package
names, descriptions, versions and dependency relationships are read from the
existing Debian recipe. The JSON is generated under `bazel-out`, never maintained
as another source file. Keep the public label when replacing an old JSON export,
so image consumers can continue to depend on it.

For example, the integration test's changelog declares version `2.0`. Its
`metadata-library` TAR contains a library that calls libc, and `metadata-cli`
contains a program that uses that library. Debian's tools produce the library's
`libc6` dependency and the program's `metadata-library (>= 2.0)` dependency from
those actual target binaries. Editing the changelog or changing linked libraries
invalidates this action and regenerates the controls.

`dependencies` supplies other source owners' generated metadata targets.
`apt_packages` maps the data target to the matching control target for each pinned
Debian library used by this build. These inputs use the target configuration.
The compiler's libc, libgcc and libstdc++ packages are supplied automatically from
the existing shared sysroot package set. Debian tools and their private runtime
use the execution configuration; they cannot supply missing target libraries.
The metadata action inspects target ELF files and never executes them.

The action stages declared TARs with the BSD tar toolchain, generates source
library symbols using `dpkg-gensymbols`, resolves `${shlibs:Depends}` using
`dpkg-shlibdeps`, and writes final controls with `dpkg-gencontrol`. Existing
`symbols` and `substvars` files may be supplied by their producing targets.
Additional Debian helper inputs, such as package templates, use `debian_files`
with paths relative to `debian/`. Standard Python, Perl, debconf and library
helper substitutions are generated from private copies of the staged files.
Unknown or unresolved substitutions fail the build. Standard empty `misc`
defaults apply only when no helper produced a value; supplied values are retained.

Owners that also hand off maintainer scripts and conffiles can opt in with
`control_file_helpers`, an ordered list of concrete helper argument lists. Keep
original sources, including their `#DEBHELPER#` markers, in `debian_files`.
For a recipe using the compat-10 systemd sequence and `dh_installinit -r`:

```starlark
control_file_helpers = [
    ["dh_systemd_enable"],
    ["dh_installinit", "-r"],
    ["dh_systemd_start"],
    ["dh_installdeb"],
],
```

The owner supplies the original compatibility file, maintainer sources and
configured service/init inputs through `debian_files`; service names and upgrade
options remain owner data. The shared action permits only the concrete
`dh_systemd_enable`, `dh_installinit`, `dh_systemd_start`, `dh_installsystemd` and
`dh_installdeb` metadata helpers. Arguments are passed directly, without a shell,
and the sequence must finish with `dh_installdeb`. Existing non-service helper
substitutions and the declared sequence run on one private payload copy. The
sequence replaces automatic service-helper selection for that call, and the
final substitution values are read once.

Each package then has a `control_files` JSON map. Its keys are final `DEBIAN`
member names; values contain `path` relative to the action's analysis tree,
`sha256` of the real file bytes, and its generated octal `mode`. Files are
available through the existing `debian` output group and
`RuntimePackagesInfo.trees`. The recorded mode is the packaging mode; Bazel may
normalize physical permissions when caching a tree artifact. Only generated
control files are retained from the private helper copy. The original runtime
TAR and its staged payload bytes remain unchanged.

An empty `control_file_helpers` list is the default and preserves the prior
substitution-only behavior and JSON shape. These files are a separate producer
handoff: owner metadata and installed-package policy accept the additional JSON
field but do not run or install these scripts. A downstream packager or installer
must explicitly consume the files. Do not install the entire dependency-analysis
tree as a runtime layer. This action does not run maintainer scripts, create
accounts, start services, or produce a DEB.

Some source owners already provide finalized binary controls with their own
`Version` instead of a source control and changelog. Those can omit `changelog`;
they must contain a single package and no unresolved placeholders. Existing
finalized controls keep their declared script/service dependencies; staging an
init script or systemd unit does not rerun source-recipe helpers or require a
fabricated debhelper compatibility level. ELF dependency analysis still checks
the actual target payload and its declared libraries. Existing
build-time version adjustments, such as FIPS's `+fips`, belong in the existing
source-preparation recipe before metadata generation. Do not copy an adjusted
version into this rule. Use `runtime_packages_group` to publish packages with
separate existing recipes through one owner target.

Generated metadata records the target architecture and actual payload hashes.
The accompanying private package trees carry controls and symbol information
through `RuntimePackagesInfo` for downstream dependency analysis. They are not
image layers. This process creates no `.deb`, runs no maintainer scripts and
leaves the legacy Debian packaging path unchanged.

```starlark
load("@sonic_build_infra//oci:installed_packages.bzl", "installed_packages")

installed_packages(
    name = "installed_packages",
    base = ":base",
    architecture = "amd64",
    apt_receipt = ":selected_apt_receipt",
    controls = ["@image_apt//example:control"],
    source_spec = "required_source_paths.json",
    sources = {"libswsscommon": "@sonic_swss_common//dist:libswsscommon_pkg"},
    owner_metadata = ["@sonic_swss_common//:runtime_packages.json"],
    source_modules = {"sonic-swss-common": "@source_modules//:sonic-swss-common.MODULE.bazel"},
)
```

The generated metadata schema includes `{"schema":1,"module":"sonic-swss-common","packages":{
"libswsscommon":{"runtime_target":"//dist:libswsscommon_pkg","control_fields":{
"Package":"libswsscommon","Version":"1.0.0"}}}}`. Architecture, payload hashes and installed state are derived by the build;
do not duplicate them in source declarations.

A producer can add `required_paths` to its `runtime_packages` call. For example,
Common can require `usr/lib/{multiarch}/libswsscommon.so.0` and
`var/run/redis/sonic-db/database_config.json` in its runtime TAR. An omitted file
fails metadata generation, even if compilation and TAR creation succeeded. These
are reviewed expectations, not a file list discovered from the same TAR.
`{multiarch}` expands to `x86_64-linux-gnu` on AMD64 and `aarch64-linux-gnu` on
ARM64. Absolute paths, parent traversal and other placeholders are rejected.

The generated owner metadata carries these requirements with the payload hash.
`runtime_packages_group` preserves them. Image `source_spec` may now contain a
sparse package map: packages whose producer checks suffice need no image entry.
For example, config-engine adds only its installed location:
`{"schema":1,"packages":{"libswsscommon":{"required_paths":["run/redis/sonic-db/database_config.json"]}}}`.
The producer still checks the raw `var/run` path. The image combines both sets and
applies its already-checked directory normalization before checking the TAR.

| Input or action | Before | After |
| --- | --- | --- |
| Common TAR omits `libswsscommon.so.0` | Each image must repeat the library path to detect this | The producer rejects the incomplete TAR before publishing metadata |
| Config-engine requires its database configuration under `/run` | The image repeats common package files alongside this requirement | The image keeps this integration check and inherits the producer's file checks |
| A package without a checked source receipt has neither producer nor image requirements | The image check rejects an empty required-path list | The combined check still rejects missing coverage |

Producer `required_paths` is optional for existing callers. Unmigrated images may
retain their complete path policies. Image additions cannot replace producer
checks; unknown image package names, missing owner metadata, changed TAR hashes
and missing required paths remain errors. Legacy typed path maps, such as Syncd’s ELF/file/symlink assertions, retain their
classifications; producer presence checks remain additional requirements. Installed-image
receipt fields are unchanged.

The rule verifies the declared runtime target belongs to the same repository as
its metadata. It reads the selected MODULE declaration for version/provenance,
hashes actual TARs and rejects missing paths or unsatisfied Debian dependencies.
`source_modules.bzl` exposes MODULE files that owners do not export themselves.
An unversioned root `sonic-buildimage` module uses `workspace` provenance; its
actual payload hash and the consumer CI revision identify that source checkout.

The default output is a status file; output group `metadata` supplies the JSON
receipt. Existing complete source contracts remain supported. Optional
`normalization_receipt`, `source_receipt` and `retained_manifest` preserve checked
installed paths, modes and ownership; the latter two are mutually exclusive.
APT/base hashes, source payload hashes and independent contract identities must
agree. The rule runs no package installation scripts and creates no DEB packages;
it records installed contents rather than performing a package-manager transaction.

With a checked `source_receipt`, required paths may be empty, for example for a
development-header TAR whose producer declares no optional path requirements.
The receipt must still bind the contract, base, APT selection, source identities,
raw TAR hash and a nonempty installed inventory. Any declared paths must exist.
Without `source_receipt`, the combined required-path list must remain nonempty;
`retained_manifest` alone does not relax this requirement.

Owner runtime targets may be aliases. The metadata macro records the requested public label using the calling package's repository mapping, while the receipt hashes the actual TAR provided by that target.
