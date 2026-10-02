# Debian packaging

`sonic_deb` stages its `data` tar into a private directory, writes `DEBIAN/control`
and `DEBIAN/md5sums`, and runs `dpkg-deb --build`. Python's standard tar extractor
creates missing directories and restores late directory metadata. Staging defers
hardlinks until their targets exist. `dpkg-deb` writes the Debian archive and its directory entries; the
rule does not reconstruct tar headers or assemble an `ar` archive itself.

The packaging action selects `//deb:dpkg_deb` with `cfg = "exec"`. Its `dpkg`,
GNU `tar`, `coreutils` and transitive runtime payloads come from the shared
`build_tools` APT set at the immutable Debian snapshots in `MODULE.bazel`.
The launcher checks dpkg-deb 1.22.22 and starts the programs with their declared
dynamic loader and libraries. Its helper PATH contains only the declared `tar`
and `rm`; runtime checks reject fallback to host libraries. Bazel's declared
Python runtime supplies staging and checksum generation.

The package's `Architecture` still comes from the **target** platform. The
packager runs on the **execution** platform and does not execute payload files.
Packaging itself needs no C++ toolchain. AMD64 and ARM64 execution are supported;
target architecture values are AMD64, ARM64 and ARMHF.

The rule retains file contents, modes, symlinks and hardlinks. It uses root
ownership, gzip for both Debian tar members, one compression thread and
`SOURCE_DATE_EPOCH=0` for deterministic timestamps. Explicit dependency metadata
comes from `depends`; this rule does not perform debhelper's automatic shared
library dependency discovery. Non-root package ownership, maintainer scripts
and extra control fields are outside the current API.

Run `//deb:build_deb_test` for extraction, checksum, link, sparse-file and
reproducibility regressions. [Source CI](../ci/README.md) also installs and runs
a sample runtime package with its matching detached debug package on native
AMD64 and ARM64.
