# Keep SONiC base packages when adding APT image layers

`rules_distroless` owns package resolution, checked package locks, downloads,
extraction and tar assembly. `apt_layer` adds SONiC's image policy: keep packages
supplied by the base image or Make, and reject replacement of base libraries and
conflicting selected libraries. For example, adding a routing package must not
replace the base image's OpenSSL library with a different Debian build.

The runtime/debug images in sonic-buildimage PRs #13 (syncd-vs) and #15
(orchagent) use this rule. It consumes existing Debian packages and produces a
tar layer; it does not build DEBs or run package installation scripts.

## Declare the checked package set with Distroless

In `MODULE.bazel`, import a checked canonical Distroless version-2 lock:

```starlark
apt = use_extension("@rules_distroless//apt:extensions.bzl", "apt")
apt.from_lock(
    name = "my_image_packages",
    lock = "//dockers/my-image:apt.lock.json",
    dependency_set = "runtime",
)
use_repo(apt, "my_image_packages")
```

The public `@my_image_packages//:package_set` target supplies the complete
package metadata, data and control archives, target architecture and exact lock
file. SONiC does not reconstruct private repository names or keep a generated
list of package keys. Ordinary builds import the reviewed versions and hashes
without resolving APT indexes again.

For an intentional update, resolve the desired packages with Distroless's
`apt.sources_list` and `apt.install` in a preparation workspace, export that
hub's public `:lock.json`, and review the changed versions, sources and hashes.
Keep any reviewed extracted-content hashes required by the image's Make handoff
with the corresponding packages in that canonical lock.

## Apply the image policy

```starlark
load("@sonic_build_infra//apt:apt_layer.bzl", "apt_layer")

apt_layer(
    name = "apt_runtime",
    base = ":base_oci",
    package_set = "@my_image_packages//:package_set",
    retained_manifest = ":native_packages_manifest",
    selector = ":select_image_packages",
    variant = "runtime",
)
```

The selector is an image-owned `py_binary` depending on
`@sonic_build_infra//apt:lib`. It validates its manifest and base OCI platform,
then calls `sonic_apt.selection.select` with its existing archive inventory
reader. `inspect_payload(path)` rejects unsafe archive entries and returns
relative path metadata. `check_overlay(entries, base_files)` rejects paths
crossing non-directories or unexpected links. Inventories use `kind`, `mode`,
`uid`, `gid`, `linkname` for links and `size`, `sha256`, `elf_type`,
`elf_machine` for ELF files.

The rule supplies `--base`, `--lock`, `--retained-manifest`, `--mapping`,
`--variant`, `--out-manifest`, and `--receipt`. The adapter writes the selected
archive paths, one per line in selection order, and its JSON receipt. Upstream
`flatten(tar_manifest=...)` checks that every selected path is a declared input
and creates the tar. The public target exposes that tar; output group
`selection` exposes the receipt.

Group names, platform checks, Make manifests and feature checks belong to the
image. `selection.base_packages` reads installed package versions from OCI
layers. Retention is by package name, including when the base version differs;
image validation must separately check runtime compatibility. Optional reviewed
payload/control hashes are verified before selection; receipts always include
actual hashes. The syncd handoff additionally binds those hashes and the exact
lock identity to its prepared package state.

## Tests

`//apt:selection_test` checks SONiC package retention, base dpkg status, library
collision checks and receipts using tar/JSON inputs. Generic locked-import and
manifest-assembly tests belong to Distroless. Image consumers also validate
this rule through their OCI integration and installed-image checks. None of
these tests needs to produce a Debian package.

## Dependency setup

The patched Distroless APIs come from upstream 0.9.4 plus the patches maintained
in this repository. Each root build must apply the override described in
[the patch instructions](../third_party/rules_distroless/README.md). The default
registry remains `main`; no temporary registry branch or separate Distroless
registration is required. Image consumers reference the infra-owned patch files
by immutable URL and checksum instead of copying them.
