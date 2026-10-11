# GCC links from configure scripts

`bazel test //toolchains/gcc/tests:foreign_link_test` runs a real
`rules_foreign_cc` configure action. The small C project links from a nested
build directory, calls the GCC stack-unwinding library, and runs the resulting
program. CI runs this test on native AMD64 and ARM64 Debian Trixie.

The test records the assembler, linker, development-library and runtime-library
paths selected by GCC, the actual backend command printed by `-Wl,--version`,
and the linker's input map. These must identify the
declared downloaded tools and packages. A host `/usr/bin/ld` fallback or a
missing `libgcc_s` search directory fails the test.

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
-B /bazel/external/gcc/x86_64-linux/bin/
-L /bazel/external/libgcc-14-dev/usr/lib/gcc/x86_64-linux-gnu/14/
-L /bazel/external/libgcc-s1/usr/lib/x86_64-linux-gnu/
```

GCC uses the downloaded tools and finds `libgcc_s.so` and `libgcc_s.so.1` in
the imported packages. The probe links and runs successfully.

The normal link arguments include the Trixie package paths beneath `usr/lib`
and the `libgcc-14-dev` directory. `rules_foreign_cc` can convert these declared
paths to absolute paths before changing directory. GCC also receives a `-B`
directory prefix for the downloaded, CPU-specific assembler and linker aliases.
GCC's driver accepts a filename prefix, but its `collect2` link helper searches
directories; using the aliases keeps both stages on the declared tools.
