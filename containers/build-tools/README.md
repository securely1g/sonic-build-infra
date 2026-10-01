# Pinned build tools container

This execution image installs the shared `build_tools` programs at their normal
Debian paths. SAI can run its original `/usr/bin/aspell` spell checker, including
the installed English dictionaries, without relocating Aspell or patching its
launcher. The image also includes Doxygen, Perl, Python, Bazel 8.5.1, and the
compiler, inspection and package-test tools used by registry CI.

## Reproducible inputs and identity

- The Debian Trixie base is pinned by digest.
- APT uses the same immutable Debian `20260727T143429Z` and security
  `20260726T121236Z` snapshots as the shared module. Priority 1001 and an explicit
  downgrade step align packages already in the newer base with those snapshots.
- Aspell `0.60.8.1-4`, English dictionaries `2020.12.07-0-1`, and Doxygen
  `1.9.8+ds-2.1` are additionally selected by exact version, preserving the SAI
  generator baseline. The snapshot pins the other package versions and closure.
- Each architecture's Bazel release executable has its own SHA-256 check.
- `/etc/sonic-build-tools.json` records schema version 1, execution architecture,
  recipe SHA-256, snapshots, Bazel version, and the complete installed package
  inventory. `identity.py` hashes the names and bytes of the Dockerfile, package
  list, identity writer, and smoke test; workflow and documentation edits do not
  change this recipe identity.

The marker identifies the recipe; it is **not** the OCI image digest. An image
cannot contain its own final digest. Consumers pin the published image by digest
and check the expected marker recipe and execution architecture separately.

## Native publication

The **Build tools container** workflow builds and tests on native hosted AMD64
and ARM64 machines. It runs Doxygen, exercises Aspell's English dictionary with
both a known and an unknown word, checks the language runtimes and compiler/test
tools, and validates the marker. No emulator is used.

Same-repository PR runs and `master` pushes publish native images to
`ghcr.io/securely1g/sonic-build-tools` using the workflow's package-write token.
Fork PRs build and test without publishing. After both native jobs pass, the
workflow creates one AMD64/ARM64 index. The `build-tools-release` artifact and
job summary contain `release.json`: the immutable index and architecture digests,
recipe identity, and source revision. Each native artifact contains the installed
inventory and smoke-test result. Tags identify source revisions for navigation;
consumers must use the digest references.

Verify anonymous pulls before using a digest in public registry CI. GitHub
package visibility depends on the creation and repository-inheritance settings;
if needed, set this package to public once in its package settings. The source
label connects this image to the public infrastructure repository.

## Consumer contract

Run metadata generation in this exact image on an execution machine matching the
image architecture. Target architecture may differ: the generator remains an
execution-side tool and generated C/C++ uses the target toolchain. An image is
not an installable target sysroot or a SONiC runtime container.

Installing tools in CI alone does not make them Bazel action inputs. The consumer
must include the image digest and expected recipe in its generator action key,
validate the marker before execution, and configure any remote executor to use
the same digest (for example, the executor's `container-image` property). Merely
setting that property does not launch a container for local execution. Registry
CI must start its job in the pinned image; local users can use the helper:

```bash
/path/to/sonic-build-infra/containers/build-tools/run.sh \
  ghcr.io/securely1g/sonic-build-tools@sha256:INDEX_DIGEST \
  build --platforms=@sonic_build_infra//platforms:x86_64_trixie @sai//:metadata
```

Run this from the consumer checkout. The helper requires an immutable digest,
pulls it, checks the image tools and marker, and supplies
`SONIC_BUILD_TOOLS_IMAGE` and `SONIC_BUILD_TOOLS_RECIPE` in the Bazel action,
host-action and test environments. It creates a
fresh temporary Bazel cache inside the container and preserves only files the
build writes into the mounted checkout. For ARM64 use a native ARM64 machine
and the appropriate target platform. The SAI generator must independently check
its expected recipe/image identity; this helper does not replace that check.

To update tools, edit the snapshot/package inputs, build both native images,
compare SAI's generated-output baselines, and update consumers to the new
immutable digest and recipe together. Keep previous image digests available for
consumers pinned to them.
