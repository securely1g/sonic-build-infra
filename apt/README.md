# Automatically keep base packages with ordinary Distroless inputs

`selection.py` keeps packages already supplied by the base image or the image's
declared Make package manifest. It adds the remaining packages from the reviewed
dependency set. Distroless supplies the package archives and assembles the layer
using its existing APIs; no APT patch is needed.

## Directory layout

- `sonic_apt/` contains the shared Python package, imported as `sonic_apt`.
  Its pinned dependency hub supports the infrastructure Python 3.11 runtime
  and the image Python 3.13 runtime using the same universal wheel.
- `tests/` contains its unit tests.
- `tests/integration/` contains Bazel/Distroless integration tests and their
  fixtures, including the isolated repository-generation check.
- The `.bzl` files in this directory expose the Bazel rules; `export_inputs.py`
  is the command-line entry point for exporting reviewed package declarations.

## Example: tcpdump and FIPS OpenSSL

The Orchagent snapshot supplies tcpdump 4.99.5-2 and Debian `libssl3t64`
3.5.6-1~deb13u2. Its config-engine base already contains `libssl3t64`
3.5.7-1~deb13u2+fips. Copying the entire dependency set would overwrite the
base's `libssl.so.3` and `libcrypto.so.3`.

The selector reads the base's `/var/lib/dpkg/status`, finds the package name
`libssl3t64`, and skips that candidate. It adds tcpdump and libpcap when their
names are absent. The FIPS library bytes stay unchanged.

**A retained package must satisfy the final image's dependency requirements.**
After selecting additions, the helper validates every known base, Make-retained
and newly selected package's `Depends` and `Pre-Depends` against that final set.
It reads dependency fields from the base's dpkg status and the declared package
control archives, using pinned `python-debian` parsing and Debian version order.
An older base that fails a required version stops the build; selection never
upgrades it automatically. A different version from the lock remains valid when
it satisfies the actual requirement, as with the FIPS OpenSSL example.

The check supports conjunctions, alternative dependencies, versioned and
unversioned virtual `Provides`, and one target architecture plus `all`.
Architecture-qualified dependencies support the explicit target and `:any`
with `Multi-Arch: allowed`. Malformed expressions, foreign architectures and
unsupported source-only restrictions or qualifiers fail explicitly. Checks also
cover the dependencies of retained packages, not only newly added packages.

`Pre-Depends` is checked for package presence and version constraints. Tar
assembly does not establish installation order, configured state of added
packages or maintainer-script execution. `Conflicts`, `Breaks`, optional
relationships and runtime ABI compatibility are outside this check; installed
image tests remain necessary. Hash, unsafe-path and inherited-library collision
errors still fail the build.

## Ordinary package inputs

Keep the canonical `apt.lock.json` with reviewed package identities, source
hashes and extracted data/control hashes. Two adapters expose those inputs to
Bazel:

- `export_inputs.py` generates the checked-in `apt_inputs.MODULE.bazel`, which
  calls standard `apt.install` with exact versions and architectures for every
  candidate in the reviewed dependency sets.
- The `apt_inputs` repository rule reads that same lock during Bazel setup and
  generates `apt_inputs.bzl` in an external repository. It maps lock keys to
  public package labels such as `@image_apt//tcpdump`; the layer adapter uses
  their existing `:data` and `:control` targets.

These are exports of one canonical lock, not another lock or a manually curated
list of packages missing from the base. They include base packages such as
OpenSSL; the selector still removes those automatically during the build.
Distroless uses the root/dependency module's pinned snapshot sources and its
normal resolver/importer. The selection action verifies the complete candidate
set and the reviewed extracted-content hashes before choosing payloads.

Generate the MODULE declaration after an intentional lock update:

```sh
python3 PATH_TO_INFRA/apt/export_inputs.py \
  --lock dockers/my-image/bazel/apt.lock.json \
  --module dockers/my-image/bazel/apt_inputs.MODULE.bazel
```

Commit the lock and MODULE declaration together. `--check` verifies the
declaration without changing it; the image's owner tests should perform the same
check. In the root MODULE, include it and declare the metadata repository:

```starlark
include("//dockers/my-image/bazel:apt_inputs.MODULE.bazel")

apt_inputs = use_repo_rule("@sonic_build_infra//apt:inputs_repository.bzl", "apt_inputs")
apt_inputs(
    name = "my_image_apt_inputs",
    lock = "//dockers/my-image/bazel:apt.lock.json",
)
```

Bazel regenerates the label mapping when the lock changes. The repository rule
only reads JSON and writes metadata: it executes no programs, resolves no
dependencies, and downloads or extracts no packages. It checks the complete
dependency closure, package identities, architectures and duplicate names.
Keep the package request recipe used for intentional refreshes separate from
these resolved inputs. The export command does not resolve a fresh lock or add
extracted-content hashes.

Generated Bazel files start with `AUTO-GENERATED. DO NOT EDIT MANUALLY.` and
name their generator. Generated JSON inputs and selection receipts carry the
same notice in `_generated` metadata because JSON does not support comments.
Keep that metadata in reviewed package locks too, with the package-resolution
and hash-preparation origin. Change package requests through the documented
refresh procedure and regenerate derived files.

## Selection and assembly

```starlark
load("@sonic_build_infra//apt:apt_layer.bzl", "apt_layer")
load("@my_image_apt_inputs//:apt_inputs.bzl", "APT_INPUTS")

apt_layer(
    name = "apt_runtime",
    packages = APT_INPUTS["image_apt"]["amd64"],
    lock = "//dockers/my-image/bazel:apt.lock.json",
    dependency_set = "image_apt",
    base = ":base_oci",
    retained_manifest = ":native_packages_manifest",
    selector = ":select_image_packages",
    variant = "runtime",
    architecture = "amd64",
)
```

The image owns its base/manifest reader and archive checks. Its selector calls
`sonic_apt.selection.select`, then `stage_payloads(selected, output_directory)`.
`base_packages` returns complete `Package` dependency records. Each explicitly
Make-retained package supplies `source_sha256` and a `control` mapping containing
its actual Debian fields, including `Package`, `Version`, `Architecture`, and
any `Depends`, `Pre-Depends`, `Provides` or `Multi-Arch` fields. A package name and
source hash alone are insufficient. Retained metadata must agree with an
already inherited package of the same name.
The command receives `--base`, `--lock`, `--retained-manifest`, `--mapping`,
`--variant`, `--out-dir` and `--receipt`.

An image can use one shared selector executable and supply its declarative
configuration with `policy = ":apt_policy.json"`. The rule declares that JSON
file as an action input and passes it with `--policy`; the selector owns its
schema and validation. `retained_manifest` may be omitted for policies that
retain no Make-produced packages. When both are supplied, the action passes
both files separately, allowing static policy to validate a generated Make
manifest. At least one of `policy` or `retained_manifest` is required. Existing
manifest-only callers keep the same selector arguments and archive outputs.

The adapter copies selected archives intact into one declared output directory.
Numeric filenames preserve their order; a valid empty archive handles an empty
selection. Bazel expands this directory for ordinary `flatten(tars=[...])`,
which creates the tar. There is no custom tar merger or generated-manifest
extension. Staging adds a copy of selected archives and its associated disk I/O.
The `selection` output group exposes the JSON receipt.

The receipt includes the validated final control inventory. When an image
inherits a previous APT layer, pass that layer's `selection` receipt as the
`apt_layer(base_package_metadata = ...)` input and forward the selector's
`--base-package-metadata` argument to `select(base_package_metadata=...)`.
This is required because adding tar payloads does not update dpkg status.
The child checks the receipt against the current dpkg baseline, preserves its
known records and validates the combined set again. For example, a debug layer
must receive the runtime APT layer's receipt so its additions and requirements
remain visible. The metadata is a declared build input, not a file installed
inside the container.

When a child intentionally replaces a package from an inherited tar layer,
the consumer may pass `retained_replacements={name: previous_control}` to
`select`. Each entry must match all dependency control fields serialized in the
inherited receipt (`Package`, `Version`, `Architecture`, `Depends`, `Pre-Depends`,
`Provides` and `Multi-Arch`),
must name a declared retained package and must change that record. This cannot
replace any package in the base's installed dpkg inventory. Other metadata
conflicts remain errors, and the final dependency and architecture checks still
apply. The receipt records the old/new controls and retained source hash in
`replaced_inherited`; the original inherited receipt stays unchanged.

The syncd debug image uses this transition for its Make-built FIPS OpenSSH
package after verifying the exact source, payload and control hashes, AMD64
architecture, `+fips` version and unchanged dependency fields. The image owns
that replacement policy and its payload checks; this helper only performs the
explicit inventory transition. The option is not permission to replace arbitrary
files or installed packages, and does not modify dpkg status.

Group names, OCI platform checks, Make manifests and file-safety policies remain
with the image. The shared helper keeps base/Make package names, avoids duplicate
names and rejects unintended inherited ELF changes. Importing archive files
does not run installation scripts or update the inherited dpkg database.

## Validation and dependency

Use `rules_distroless` **0.9.4.sonic.1**. It includes the existing Protobuf `.inc`
header fix and sorts above plain 0.9.4, so that competing request needs no root
override. The dotted registry entry preserves the code and patches from
0.9.4-sonic.1. No local archive patches are required.

`//apt:dependencies_test` checks Debian version order, alternatives, virtual
providers, supported architecture qualifiers and malformed metadata.
`//apt:selection_test` checks retention, content hashes, file collisions,
control identities and inherited dependency inventories.
`//apt:inputs_test` checks export consistency, the complete dependency set and
archive staging. Source CI also runs
`python3 -B apt/tests/integration/inputs_repository_test.py --bazel bazel` on native
AMD64 and ARM64. Its isolated workspace uses Bazel queries with downloads
disabled to check cold metadata generation, lock-change invalidation, dependency
cycles and invalid locks. `//apt/tests/integration:adapter_test` runs the adapter with real
public Distroless data/control targets and standard flatten, including an empty
selection when the base already supplies the package. These tests produce tar
archives, not Debian packages. Image consumers additionally check the completed
runtime/debug filesystems and installed programs.
