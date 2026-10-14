# A repository sysroot for compiler toolchains

`sysroots.debian` exposes the same pinned Trixie libc, Linux headers, GCC runtime,
and C++ headers used by SONiC's GCC toolchain as one repository directory. This
allows compiler toolchains whose public API requires a repository source sysroot,
including `toolchains_llvm`, to use SONiC's headers and libraries. SymCrypt is the
initial consumer because its native build selects Clang.

```starlark
sysroots = use_extension("@sonic_build_infra//toolchains/sysroot:extensions.bzl", "sysroots")
sysroots.debian(name = "sonic_sysroot_amd64", architecture = "amd64")
use_repo(sysroots, "sonic_sysroot_amd64")
# Pass @sonic_sysroot_amd64//root:sysroot to the compiler's sysroot API.
```

The extension extracts existing Distroless `data.tar.xz` repository source
files with Bazel's extraction API: the seven GCC sysroot inputs plus GCC's matching
runtime dependencies (17 archives on AMD64, 16 on ARM64). This completes development
links such as `libatomic.so` and permits staging the whole tree in CMake actions.
Their checksums and versions remain owned by
the existing APT/sysroot declarations. It does not depend on an action-produced
directory. The existing pinned BSD tar tool records relative merged-/usr aliases;
Debian's own files, symbolic links, and linker scripts are preserved. The generated
`inputs.json` records the exact input labels. Linux AMD64 and ARM64 hosts and
targets are supported.

This API does not register or replace a compiler. Consumers should scope compiler
selection to the component that needs it. The independent test consumer uses
maintained `toolchains_llvm` 1.11.2 and LLVM 19.1.7, matching the upstream compiler
version of the SONiC Trixie builder's Debian Clang 19.1.7 package; Debian's compiler
packaging patches are not claimed to be identical.

The upstream ARM64 LLVM distribution requires `libxml2.so.2` to execute its
linker. Install Debian's `libxml2` on the build host, as the native SONiC builder
and this CI workflow do. This is an execution prerequisite; target compilation
and the resulting program use only the declared sysroot headers and libraries.

Run `python3 toolchains/sysroot/tests/validate.py --artifacts /tmp/sysroot-proof`
from this repository on a native Linux AMD64 or ARM64 host. CI runs it on both
Trixie architectures. The check audits the action graph, compiles a C++ program,
verifies declared Clang/LLD and header paths, compares a linker script with its
original archive, and runs the program with the declared loader and libraries.
It also rejects absolute or dangling symlinks and host include/library paths. The test produces
no DEB packages. Changing sysroot versions belongs in the existing shared APT/GCC
declarations; the bridge has no second version map to update.
