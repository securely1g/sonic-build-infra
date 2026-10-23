# OCI image helpers

## Add service accounts

`account_layer` is a compatibility name for the public Distroless `accounts`
rule, selected through `rules_distroless 0.9.4.sonic.13`. It preserves
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
existing `debian/control`, `debian/changelog` and the runtime TARs. Images declare
only required paths. Changing a version in the producer's changelog changes the
image inventory without editing another copy of the package controls.

```starlark
load("@sonic_build_infra//oci:runtime_packages.bzl", "runtime_packages")

runtime_packages(
    name = "runtime_packages.json",
    control = "debian/control",
    changelog = "debian/changelog",
    packages = {"example": "//dist:runtime"},
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

Some source owners already provide finalized binary controls with their own
`Version` instead of a source control and changelog. Those can omit `changelog`;
they must contain a single package and no unresolved placeholders. Existing
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
do not duplicate them in source declarations. The image policy is
`{"schema":1,"packages":{"libswsscommon":{"required_paths":["usr/lib/{multiarch}/libswsscommon.so.0"]}}}`.

For AMD64 this checks `usr/lib/x86_64-linux-gnu/libswsscommon.so.0`; for
ARM64 it checks `usr/lib/aarch64-linux-gnu/libswsscommon.so.0`.

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

Owner runtime targets may be aliases. The metadata macro records the requested public label using the calling package's repository mapping, while the receipt hashes the actual TAR provided by that target.
