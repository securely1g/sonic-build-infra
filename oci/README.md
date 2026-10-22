# OCI image helpers

## Add service accounts

`account_layer` preserves `/etc/passwd` and `/etc/group` from the checked OCI
base and appends only the declared service accounts. For example, the base image
adds Redis, and Syncd adds the `_ssh` group without copying Debian's account list.
Public Distroless rules serialize additions; the shared merger checks them against
the base; the public TAR rule packages the two resulting files.

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

## Record installed package identities

`installed_packages` builds dpkg status and a package receipt from the checked
base, selected Debian controls and source-owned runtime TARs. Source repositories
declare their own `runtime_packages.json`; images declare only required paths.
For example, changing Common's package version changes the image inventory without
editing a second copy of Common's controls in every container.

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

The metadata schema is `{"schema":1,"module":"sonic-swss-common","packages":{
"libswsscommon":{"runtime_target":"//dist:libswsscommon_pkg","control_fields":{
"Package":"libswsscommon","Version":"1.0.0"}}}}`. Do not freeze Architecture,
installed hashes, Status, Conffiles or Installed-Size there. The image policy is
`{"schema":1,"packages":{"libswsscommon":{"required_paths":["usr/lib/libswsscommon.so.0"]}}}`.

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
