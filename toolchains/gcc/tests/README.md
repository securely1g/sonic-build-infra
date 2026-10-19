# GCC links from configure scripts

`bazel test //toolchains/gcc/tests:foreign_link_test` runs a real
`rules_foreign_cc` configure action. The small C project links from a nested
build directory, calls the GCC stack-unwinding and math libraries, and runs
both dynamic and static executables. CI runs this test on native AMD64 and
ARM64 Debian Trixie.

The test records the assembler, linker, development-library and runtime-library
paths selected by GCC, the actual backend command printed by `-Wl,--version`,
and the linker's input map and dependency file. These must identify the declared downloaded tools
and the architecture's shared SONiC sysroot. The test checks that `libgcc_s.so`,
`libgcc_s.so.1` and `libc.so.6` all come from that same directory and that no
linker input uses the old per-package APT directories. A host `/usr/bin/ld`
fallback or a missing `libgcc_s` search directory fails the test. Both math
link modes must also open the libraries from this sysroot. This catches host
paths in Debian's `libc.so`, `libm.so` and `libm.a` linker scripts; the shared
sysroot makes those paths explicitly relative with the `=` prefix.

The compiler check completes during configure, as an Autoconf compiler probe
does. `targets = []` skips Make; the registered development-only Make and
pkg-config metadata is never executed. This isolates the compiler regression
from bootstrapping those tools. The test produces an executable and text
receipts; it does not create Debian packages.

## Before and after

A package such as `socat` checks its C compiler during configure. This test
recreates that check from a nested directory:

```sh
(cd nested/build && $CC $CFLAGS probe.c $LDFLAGS -lgcc_s -o probe)
```

**Before:** downloaded GCC could invoke `/usr/bin/ld`, and linking failed with
`cannot find -lgcc_s`.

**After:** the toolchain supplies these paths (AMD64 example, shortened):

```text
-B /bazel/external/gcc/x86_64-linux-gnu/bin/
--sysroot=/bazel/external/sonic_sysroot/root
-L /bazel/external/sonic_sysroot/root/usr/lib/gcc/x86_64-linux-gnu/14/
-L /bazel/external/sonic_sysroot/root/usr/lib/x86_64-linux-gnu/
```

GCC uses the downloaded tools and finds `libgcc_s.so`, `libgcc_s.so.1` and libc
in the shared sysroot. Clang uses the same directory, with the same pinned
Debian headers and libraries. The probe links and runs successfully.

The normal link arguments point beneath the shared sysroot's `usr/lib`
directory. `rules_foreign_cc` can convert these declared paths to absolute paths
before changing directory. GCC also receives a `-B`
directory prefix for the downloaded, CPU-specific assembler and linker aliases.
GCC's driver accepts a filename prefix, but its `collect2` link helper searches
directories; using the aliases keeps both stages on the declared tools.

## Imported shared-library chains

`//toolchains/gcc/tests/runtime:transitive_runtime_test` links an executable to
an imported middle DSO, which imports a leaf DSO from another package. This
matches consumers such as SWSS Common and sonic-db-cli. The executable has no
direct reference to the leaf, so the runtime loader must find a transitive
`DT_NEEDED` entry through Bazel's generated library search paths.

Debian's linker defaults to `DT_RUNPATH`, unlike the previous GCC distribution's
`DT_RPATH`. GCC explicitly retains `DT_RPATH` so imported DSOs remain usable
after Bazel places them in `_solib`. A consumer that intentionally needs
`DT_RUNPATH` may still pass `-Wl,--enable-new-dtags` in its link options.
