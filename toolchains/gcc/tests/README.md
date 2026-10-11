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

The normal link arguments include the Trixie package paths beneath `usr/lib`
and the `libgcc-14-dev` directory. `rules_foreign_cc` can convert these declared
paths to absolute paths before changing directory. GCC also receives a `-B`
directory prefix for the downloaded, CPU-specific assembler and linker aliases.
GCC's driver accepts a filename prefix, but its `collect2` link helper searches
directories; using the aliases keeps both stages on the declared tools.
