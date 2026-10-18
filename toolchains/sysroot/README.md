# One SONiC sysroot for GCC and Clang

GCC and Clang use the same pinned Trixie system headers and libraries from one
multiarch repository directory for AMD64 and ARM64. For example, an AMD64 program
built by either compiler reads the same `stdio.h` and links the same libc and
C++ standard library. Each compiler keeps its own executables, built-in headers
and compiler options.

`sonic-build-infra` declares `sonic_sysroot` through
`sysroots.debian`. Consumers import that existing repository; they do not
declare a second tree or copy the package list:

```starlark
sysroots = use_extension("@sonic_build_infra//toolchains/sysroot:extensions.bzl", "sysroots")
use_repo(sysroots, "sonic_sysroot")
# Both architectures and both compilers use @sonic_sysroot//root:sysroot.
```

The extension extracts existing Distroless `data.tar.xz` repository source
files with Bazel's extraction API: the seven GCC sysroot inputs plus GCC's matching
runtime dependencies (33 archives: 17 AMD64 and 16 ARM64). This completes development
links such as `libatomic.so` and permits staging the whole tree in CMake actions.
Their checksums and versions remain owned by
the APT declarations and `packages.bzl`. It does not depend on an action-produced
directory. The existing pinned BSD tar tool records relative merged-/usr aliases;
Debian's compiled libraries, headers and symbolic links are preserved. Six
linker scripts receive the path normalization described below. The generated
`inputs.json` records the exact input labels and overlap-check counts. Linux AMD64 and ARM64 hosts and
targets are supported.

The directory contains both Debian multiarch layouts:

```text
root/usr/include/                       shared headers
root/usr/include/x86_64-linux-gnu/       AMD64 headers
root/usr/include/aarch64-linux-gnu/      ARM64 headers
root/usr/lib/x86_64-linux-gnu/           AMD64 libraries
root/usr/lib/aarch64-linux-gnu/          ARM64 libraries
root/usr/lib/gcc/<triplet>/14/           matching GCC runtime/startup files
```

Before extraction, the pinned BSD tar tool inventories each archive's file type,
permissions, owner and symlink target. Duplicate paths must agree. Regular files
are also compared byte for byte in a temporary extraction before being added to
the combined tree. A mismatch fails repository setup and names the path and both
packages. Timestamps are excluded: they describe package build times, not ABI or
file contents. The current pins have 3,752 paths shared across architectures
(2,503 files, 1,052 symlinks and 197 directories) and no conflicts; 14 directories
have different timestamps. There is no architecture-wins overwrite policy.

The default GCC toolchain now consumes this same directory using upstream
`rules_cc`'s `cc_sysroot` support. Its old per-package directory map and fixed-depth
relative paths are removed. GCC still
needs explicit paths for Debian's multiarch headers and startup files because
the compiler distribution uses a different target-directory name. These paths
all stay inside the shared root.

During repository setup, the extension changes absolute file references in
Debian's `libc.so`, `libm.so` and `libm.a` linker scripts into explicit sysroot
references. For example, `/lib/x86_64-linux-gnu/libc.so.6` becomes
`=/lib/x86_64-linux-gnu/libc.so.6`. Both GNU ld and LLVM's LLD interpret the `=`
prefix as "inside the directory passed through `--sysroot`."

This small change is needed because Bazel's sandbox supplies input files as
symbolic links. When GNU ld resolves those links, it can mistake an unchanged
Debian script for a file outside the sysroot and load libraries from the host.
The explicit prefix removes that ambiguity, including for static math libraries.
Both compilers read the same normalized scripts; GCC needs no separate package
layout or linker input remapping. The GCC foreign-build test checks the actual
linker map to reject host library paths.

Custom `gcc.debian_toolchain` declarations must pass `sysroot` as the shared
repository label, alongside their existing `suite` and architecture. The
underlying `sonic_host_toolchain` macro likewise takes one sysroot label rather
than a map of package directories. Consumers using the default registered GCC
toolchains need no changes. Consumers using the earlier per-architecture API should replace both imports
with `sonic_sysroot` and remove `architecture` from any `sysroots.debian` tag.
Repeated identical declarations reuse the same repository.

Importing a sysroot does not select a compiler. Consumers should scope compiler
selection to the component that needs it. SymCrypt is the initial Clang consumer.
The independent test consumer uses
maintained `toolchains_llvm` 1.11.2 and LLVM 19.1.7, matching the upstream compiler
version of the SONiC Trixie builder's Debian Clang 19.1.7 package; Debian's compiler
packaging patches are not claimed to be identical.

The upstream ARM64 LLVM distribution requires `libxml2.so.2` to execute its
linker. Install Debian's `libxml2` on the build host, as the native SONiC builder
and this CI workflow do. This is an execution prerequisite; target compilation
and the resulting program use only the declared sysroot headers and libraries.

Run `python3 toolchains/sysroot/tests/validate.py --artifacts /tmp/sysroot-proof`
from this repository on a native Linux AMD64 or ARM64 host. CI runs it on both
Trixie architectures. For both GCC 14.2.0 and Clang 19.1.7, the check audits the
action graph, compiles C and C++, and runs the resulting programs with the
declared loader and libraries. C++ tests cover threads and exceptions across a
shared-library boundary, including a GCC caller with a Clang library and the
reverse. The validator retains each compiler's outputs before the next build,
records their hashes, and verifies that both use exactly the same sysroot
directory and input archives.

It also verifies all six linker scripts against their original archive, allowing
only the generated notice and explicit sysroot prefixes. It rejects absolute
or dangling symlinks, host include/library paths and the old per-package GCC
layout. The source-rule CI separately runs the nested configure-build regression
to verify GCC's selected assembler, linker and runtime files after changing the
working directory. These checks produce no DEB packages. Update system package
versions in the shared APT declarations and `packages.bzl`; neither compiler
keeps a separate package-version map.

CI also checks both architectures' ELF identifiers and header locations inside
this one tree. The native C probe checks an architecture-specific Linux syscall
constant, so using the other architecture's headers fails compilation. A final
job compares all source archive hashes and the complete sysroot content hash
from the two native runners. These checks execute GCC and Clang natively on each architecture. The separate
[cross-compilation checks](../gcc/README.md) build ARM64 programs with both
compilers on AMD64, then execute those outputs on a native ARM64 runner.
