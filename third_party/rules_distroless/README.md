# Patches for upstream rules_distroless

`sonic-build-infra` owns two patches applied to the upstream **0.9.4 release**.
The release archive is pinned by checksum. No Distroless fork or additional
Distroless registry version is needed.

| Patch | Why it is needed |
| --- | --- |
| `patches/0001-include-fragments.patch` | Keep the existing SONiC fix that exports Protobuf `.inc` headers, such as `google/protobuf/port_def.inc`, to C++ compilation actions. |
| `patches/0002-locked-apt-layers.patch` | Import a reviewed APT lock, expose all package data/control archives and metadata, and let the existing tar rule assemble an ordered selection. |

Each patch records its origin and purpose. The second carries the work from
[Distroless PR #1](https://github.com/securely1g/rules_distroless/pull/1), adapted
to the pinned release. The functional `apt/` and `distroless/` files are identical
to the earlier tested backport; the upstream module keeps its own identity and
only adds a lazy package input used by the import regression tests.

## Building this repository

The root `MODULE.bazel` uses `archive_override` with these local patch files.
Normal commands and CI use the maintained SONiC registry `main` for other
modules. CI tests the patched upstream APIs, checks the Protobuf headers, and
runs the SONiC image policy tests on native AMD64 and ARM64. It audits the action
graph before execution; the selected targets produce no DEBs.

## Using the patches from an image build

Bazel honors overrides only in the root module. Depending on sonic-build-infra
does **not** apply its override automatically. Bazel 8.5.1 also requires local
patch labels to belong to that root; it rejects a patch label in a dependency.

An image's root `MODULE.bazel` therefore fetches the same upstream archive and
applies these files by immutable URL and checksum. Replace `INFRA_COMMIT` below
with the exact sonic-build-infra source commit used by the image:

```starlark
bazel_dep(name = "rules_distroless", version = "0.9.4")

archive_override(
    module_name = "rules_distroless",
    urls = ["https://github.com/bazel-contrib/rules_distroless/releases/download/v0.9.4/rules_distroless-v0.9.4.tar.gz"],
    integrity = "sha256-XUP09E8PD7YUGUoHptz3spM4z8irf2EprR5EA8GtHHM=",
    strip_prefix = "rules_distroless-0.9.4",
    remote_patches = {
        "https://raw.githubusercontent.com/securely1g/sonic-build-infra/INFRA_COMMIT/third_party/rules_distroless/patches/0001-include-fragments.patch": "sha256-AKuASTTmj5ZgOn//G8fHHU5tupttzaVWZDvBc9sCAws=",
        "https://raw.githubusercontent.com/securely1g/sonic-build-infra/INFRA_COMMIT/third_party/rules_distroless/patches/0002-locked-apt-layers.patch": "sha256-hVmABwx7y/w5bdtZxwj1L3xHyouBxQ5i2cZuHE5WsVI=",
    },
    remote_patch_strip = 1,
)
```

The patch files remain here; consumers do not copy them. Keep the two entries
in order, and update their URLs and checksums together when the patch contents
change. A source override used while the infra PR is unmerged must select the
same infra commit. Registry URLs remain on `main`.

## Updating a patch

Start from the exact upstream release archive above, verify its integrity,
then apply the patches in their listed order with `-p1`. Preserve the include
fragment fix when replacing the APT patch. Record the reason and origin in the
patch header, and update this example plus affected consumer checksums.

Run `bash ci/bazel-ci.sh` in the supported native Debian Trixie build environment.
This includes the checked-import tests, ordered/empty tar assembly, three
expected manifest rejections, Protobuf header compilation, and SONiC policy tests.
Also validate a real image consumer against the published patch URLs. Retain
the resolved module metadata and compare the resulting image with its baseline
when changing only how the dependency is fetched.
