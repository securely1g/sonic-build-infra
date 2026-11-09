# Declared build-time tools

For native AMD64, `:docbook2man` generates manpages from one DocBook SGML input.
It uses the same OpenSP parser, SGMLS Perl converter and DocBook data as Debian's
`docbook-utils` man backend. Set `SOURCE_DATE_EPOCH` to the source timestamp;
missing timestamps and malformed SGML fail. The tool does not use host Jade,
SGML catalogs, Perl modules or PDF/TeX programs. The separate `:docbook_runtime`
tree supports `rules_foreign_cc` through its relocatable `bin/docbook2man`.

SONiC wpa-supplicant's native recipe runs `docbook2man` on the five manuals
shipped in `wpasupplicant`, including `wpa_cli.sgml` and `wpa_supplicant.conf.sgml`.
`docbook_runtime_test` uses those exact files at source revision `6ca19c73` and
compares every generated manual byte with the unchanged Debian backend, then
checks relocation and invalid inputs. This is a build tool; it does not produce
a Debian package or change the compiler's target sysroot. ARM and other DocBook
output formats are outside this addition.

`docbook-utils` stays in the shared `build_tools` APT set. This runtime selects
only its `:data` payload plus the declared OpenSP/SGMLS dependencies; it does
not execute its Jade, HTML or TeX backends. The kernel runtime retains its
maintained package roots and their complete dependency closure through the
separate kernel-runtime subset prerequisite. Adding DocBook tools therefore
does not add Jade, TeX/PDF or browser packages to that runtime. Both consumers
still use the same shared execution-tool package set.

Use these executable targets for generators that need Aspell or Doxygen:

| Target | Behavior |
| --- | --- |
| `@sonic_build_infra//tools/build_tools:aspell` | Aspell with the pinned English dictionary and filters; accepts ordinary Aspell arguments, including `-l en -a -p ./personal.pws`. |
| `@sonic_build_infra//tools/build_tools:doxygen` | Doxygen with its pinned shared libraries; accepts ordinary Doxygen arguments. |

Both use the existing `build_tools` APT package set, dated snapshots and package
integrities from `MODULE.bazel`. Their runtime contains the dynamic loader,
libraries, plugins and dictionaries. A declared Python action unpacks the package
payloads, compiles Debian's compressed dictionaries, and checks that runtime
libraries resolve inside the bundle. The compiler's target `sysroot` is unchanged.

## Call a tool from a rule

Select the executable in the execution configuration and pass its
`FilesToRunProvider` to the action. This declares the executable and its runfiles:

```starlark
"aspell": attr.label(
    default = Label("@sonic_build_infra//tools/build_tools:aspell"),
    executable = True,
    cfg = "exec",
),
```

```starlark
args.add("--aspell", ctx.executable.aspell)
ctx.actions.run(
    # The consuming rule supplies its own generator and declared input files.
    executable = generator,
    arguments = [args],
    tools = [ctx.attr.aspell[DefaultInfo].files_to_run],
    inputs = inputs,
    outputs = outputs,
)
```

If a generator changes working directory, convert the executable path to an
absolute path **without resolving its symlink** before invoking it. The tool
locates its own sibling `.runfiles` directory, or the enclosing runfiles tree
when used as test data. It does not require `PATH`, `RUNFILES_DIR`, a system
Python, or a system Aspell/Doxygen installation. The small `/bin/sh` bootstrap
uses shell builtins and then starts the declared `rules_python` interpreter.
Linux directory runfiles are required; manifest-only runfiles are not supported.

The launcher preserves standard streams, arguments and the caller's working
directory. Aspell's personal dictionary remains caller-owned input; declare that
file in the consuming action. The caller should use a controlled `HOME` and
locale, as the SAI generator does. No component-specific loader or library-path
environment variables are needed.

## Validation

Native AMD64 and ARM64 source CI builds both tools and the
`//tools/build_tools:launcher_action_check` action, then runs
`//tools/build_tools:runtime_test`. The action calls the executable targets
through `FilesToRunProvider`; the test calls them from its runfiles. Both use a
temporary working directory and empty `PATH`, verify English spelling and a
relative personal dictionary, and generate Doxygen XML. The CI base has no
installed Aspell or Doxygen. CI archives its generated `MODULE.bazel.lock`, logs,
test results and the action's runtime report.

```sh
bazel build //tools/build_tools:launcher_action_check
bazel test //tools/build_tools:runtime_test
```

For this repository's native ARM64 configuration, add `--config=aarch64`.

The consuming edge's `cfg = "exec"` selects the tools for the machine running
the generator. A target architecture alone must not select these executables.
Native CI does not establish remote execution or every cross-compilation pair.

## Configure and Make builds

`@sonic_build_infra//tools/build_tools:getconf` exports the pinned libc-bin
program and its private execution runtime. The foreign Autotools runtime also
provides it on its declared PATH for configure checks such as `getconf LONG_BIT`.

Use these tools when compiling an upstream source release with `rules_foreign_cc`:

| Target | Pinned executable |
| --- | --- |
| `@sonic_build_infra//tools/build_tools:make` | GNU Make 4.4.1 |
| `@sonic_build_infra//tools/build_tools:m4` | GNU M4 1.4.19 |
| `@sonic_build_infra//tools/build_tools:pkgconf` | pkgconf 1.8.1 |
| `@sonic_build_infra//tools/build_tools:autoconf` | Autoconf 2.72 with its declared Perl/M4 and macro data |
| `@sonic_build_infra//tools/build_tools:autoheader` | Generate config header templates using Autoconf 2.72 |
| `@sonic_build_infra//tools/build_tools:autom4te` | Autoconf’s macro-processing helper with relocated configuration |
| `@sonic_build_infra//tools/build_tools:bison` | Bison with its packaged parser skeletons and declared M4 |
| `@sonic_build_infra//tools/build_tools:flex` | Flex with declared M4 |
| `@sonic_build_infra//tools/build_tools:perl` | Perl 5.40.1, including core and native extension modules |

The same dated Debian snapshots supply these execution tools. GNU Make uses
explicit package URLs and checksums because the current APT resolver selects
`make-guile` when asked for `make`; this keeps the ordinary GNU Make build.
The `configure_runtime` tree contains these nine tools, their loader and shared
libraries. It is separate from the Aspell/Doxygen runtime. Consumers extract
existing package payloads; these rules never produce a Debian package.

Register the standard foreign_cc toolchain adapters in the consuming root module:

```starlark
register_toolchains(
    "@sonic_build_infra//tools/build_tools:make_toolchain",
    "@sonic_build_infra//tools/build_tools:m4_toolchain",
    "@sonic_build_infra//tools/build_tools:pkgconfig_toolchain",
    "@sonic_build_infra//tools/build_tools:autoconf_toolchain",
)
```

For a configure script that also needs Perl, add the runtime as execution data:

```starlark
configure_make(
    name = "upstream_library",
    # The component supplies its source, configure options and output files.
    build_data = ["@sonic_build_infra//tools/build_tools:configure_runtime"],
    env = {
        "PERL": "$(execpath @sonic_build_infra//tools/build_tools:configure_runtime)/bin/perl",
    },
    features = ["-sanitize_pwd"],
)
```

Disabling `sanitize_pwd` for this rule lets foreign_cc's shell wrapper remember
the original execution directory before changing directory. It does not disable
Bazel's action sandbox. Ordinary compiler rules retain their existing settings.
These adapters do not make arbitrary upstream shell scripts fully independent
of the host: foreign_cc still uses its documented shell and basic file utilities.

The Autoconf scripts use their packaged Perl libraries and macro files, plus a
private GNU grep for their M4 version probe. Their generated configuration
removes the package’s hardcoded host macro-directory options and the launcher
supplies the relocated directory instead.

Directory aliases in the tool packages are materialized before publishing the
runtime tree. This lets Bazel restore every child from its disk cache without
depending on the order in which directory symlinks are restored.

The runtime's `bin` entrypoints also work through file symlinks, including
foreign_cc's `ext_build_deps/bin/make`. A small static launcher, built with the
declared execution-platform C compiler and sysroot, reads Linux's
`/proc/self/exe` before invoking the adjacent shell wrapper. Keep the whole
runtime tree, including hidden wrapper files, when relocating it. This adds no
host `readlink` or dynamic-loader dependency; the existing `/bin/sh` bootstrap
remains required. Make, Autoconf, Automake, Libtool and the other tools still come
from their pinned Debian package payloads.

The launchers preserve the working directory and arguments. Recursive Make and
Perl calls use the same declared tools. Perl searches its packaged core modules
and caller-declared `-I` paths; it does not search the host's built-in Perl
library directories. Use `-I` for source modules and declare those files as
inputs. The launcher owns `PERL5LIB` and `PERL5OPT` to establish that runtime.
For pkgconf, provide `PKG_CONFIG_LIBDIR` or `PKG_CONFIG_PATH` for declared target
package metadata. An unset search directory does not fall back to the host's
package database.

Native AMD64 and ARM64 CI runs `configure_runtime_test` with an empty `PATH`,
including Autoconf/header regeneration, parser/scanner generation, recursive calls,
and native Perl module loading. The tests relocate the actual runtime and invoke
its tools through absolute and relative file symlinks, verify arguments and exit
status, and protect existing package aliases when installing the launcher.
They also compile and
runs a small C program linked to a library built by `configure_make_test`, proving the registered
toolchains work when foreign_cc stages their runtime tree into an action.
CI then repeats the tool runtime test in a fresh Bazel output directory, with
compilation results shared only through the disk cache. It checks the execution
log reports `PrepareBuildTools` and the kernel runtime’s `PrepareBuildToolsRootfs`
as disk-cache hits and retains that receipt.

## Kerberos error and command table generators

`//tools/build_tools:compile_et` and `:mk_cmds` expose Debian's generators for
Kerberos builds that select `--with-system-et --with-system-ss`. The separate
`:kerberos_runtime` tree contains both scripts, their templates, AWK and the
small command-line tools they invoke. A `configure_make` consumer can declare
that tree as `build_data` and prepend its `bin` directory to its action's PATH.

The launchers use the declared interpreter and templates after the tree is
copied to a new directory. They do not search the host PATH or `/usr/share` for
these inputs. The package scripts' hardcoded AWK path is relocated; their
existing template-directory overrides preserve the generator logic.
`kerberos_runtime_test` exercises both generators with an empty PATH, checks
stable generated C, and fails when the declared templates are missing. Both
native AMD64 and ARM64 jobs run this test.

## Kernel build runtime

`@sonic_build_infra//tools/build_tools:kernel_runtime` produces one directory
containing the shared `build_tools` package closure, including GCC 14, Debian
packaging programs, Kbuild utilities and their libraries. The packages use the
same dated APT snapshots and integrity checks as the generator tools. Kernel
builds and source generators share one APT package set. Adding tools to that set
also updates the kernel build filesystem and its cache identity. No kernel
binary is downloaded or embedded in this runtime.

Adding new DEB package definitions or DEB-producing targets requires explicit
approval. Ordinary builds and validation of existing, approved package producers
need no new package approval. These runtime targets extract existing Debian
package payloads and do not produce DEBs.

Keep only the required top-level package roots in `MODULE.bazel`; the APT resolver
fetches their binary `Depends` and `Pre-Depends` transitively. For example, Bash
brings in `base-files` and `mawk`, while `build-essential` brings in `bzip2`,
`patch` and `tar`. Do not repeat these or tools already declared for generators.

The selected `rules_distroless` API does not read source `Build-Depends` or
`Build-Depends-Arch`. The kernel roots are therefore an explicit mapping of the
selected Debian kernel source requirements plus the tools used by its SONiC
build. Its generated control file depends on the selected build profiles and
architecture. Retain roots such as `debhelper`, `python3`, `gcc-14` and `xz-utils`
even when their payloads are transitive: the APT hub writes dpkg status records
only for explicit roots, and the kernel runs `dpkg-checkbuilddeps`. The six
`_KERNEL_LINK_LIBRARIES` roots also expose the `:data` and `:control` labels used
to install library ownership, shlibs and symbols metadata. These entries have
direct metadata consumers; the other runtime libraries remain transitive.
The architecture-qualified `gcc-arm-linux-gnueabihf:arm64` root supplies the
compatibility vDSO compiler and linker only for the native ARM64 runtime; its
execution binaries run on ARM64 and produce ARM32 code.

The `sonic-linux-kernel` source-build rule selects this target in execution
configuration. Its `bin` directory contains relocatable wrappers for the
compiler, Debian packaging tools, interpreters and their child programs. Each
ELF tool uses the declared loader and libraries; Perl, Python, Quilt, Debhelper
and dpkg use the declared modules and data. These tools run as the ordinary
build user without Docker, chroot or device creation. The runtime marker has
`execution: "relocatable"` so consumers reject the older chroot-only interface.

Consumers set `PATH` to the runtime's `bin` directory and select its `sh`/`bash`
for Make recipes. Native `gcc` and `g++` produce ordinary deployment ELFs.
`host-gcc` and `host-g++` additionally select the private ELF loader and RPATH
for generated programs that must execute during a build. A consumer that later
packages such a program must restore its deployment interpreter and remove
private search paths before splitting debug information and producing packages.
ARM64 retains its ARM32 compatibility-vDSO compiler and binutils.

For shared-library dependency calculation, copy `var/lib/dpkg` into action-local
scratch, prefix the library `.list` entries with the runtime's actual path, and
set `DPKG_ADMINDIR` to that private database. The immutable input runtime must
not be modified. The wrappers supply `DPKG_DATADIR`, `DPKG_ORIGINS_DIR` and the
private Perl dpkg paths. Debian's supported `Rules-Requires-Root: no` behavior
preserves package ownership metadata without requiring elevated privileges.

The runtime is prepared without root, network access or package maintainer
script execution. `kernel-runtime.json` records the architecture, package
versions, input hashes and a path-independent identity. Adding or changing a
relocation helper changes that declared runtime identity.

Empty directories contain a zero-byte `.bazel-keep-directory` file so Bazel's
cache downloader preserves them. Kernel preparation keeps packaged directory
aliases such as Perl's `5.40 -> 5.40.1` and the root merged-/usr alias
`lib -> usr/lib` instead of copying their target directories.
Dependency lookup prefers canonical `/usr/lib` paths recorded in Debian package
file lists, including when a cache restores `/lib` as a real directory.

### Root download policy

For the pinned Bazel 8.5.1, the root build configuration selects the kernel
tool trees for explicit download from the action cache:

```text
build --remote_download_regex=(^|.*/)tools/build_tools/kernel_runtime_(amd64|arm64)(/.*)?$
```

This uses Bazel's upstream
[`--remote_download_regex` option](https://bazel.build/reference/command-line-reference#param-remote-download-regex).
The pattern covers either execution architecture, the tree itself and its
children, and both root-module and external-repository output paths. It does
not request every cached output as `--remote_download_all` would.

This is a **root invocation requirement**. Bazel does not import a dependency's
`.bazelrc`. Before selecting an infrastructure version without kernel alias
normalization, consumers must check this option into their own root
configuration and pass it explicitly in commands using `--ignore_all_rc_files`.
For example, Sairedis's `//:runtime_packages.json` uses this tree through the
shared metadata rule, even though the tools are not a top-level build target.
Common, FIPS, DASH and image metadata builds have the same requirement; the
registry's generated consumer workspace and standalone kernel builds are
separate roots. Keep existing normalized dependency pins until those root
configurations are updated. Documentation alone does not configure consumers.

The infrastructure root `.bazelrc`, shared source CI and declared-tools CI carry
the policy. Both CI paths specify it explicitly because they ignore RC files.

The preparation action retains Debian's library/sysroot layout, explicit
build-command alternatives, and the PurePerl XML parser registration normally
created by Debian's `libxml-sax-perl` installation script. It removes the legacy
`/usr/bin/X11 -> .` alias brought in by Graphviz so Bazel can store the tree
without following a directory cycle. It retains package status and library ownership,
shlibs and symbols metadata so `dpkg-shlibdeps` can calculate the source-built
kernel tools' dependencies. The compiler's execution-side libc headers remain
part of this runtime; they do not replace any consuming target's sysroot.

Native AMD64 and ARM64 source CI run `prepare_rootfs_test` for metadata, archive
boundaries and empty-directory/symlink preservation. `kernel_debian_test`
separately covers narrowly scoped packaging-tool relocation.
`kernel_runtime_test` checks a copied runtime at a different path. The latter
runs without chroot, parses XML through
the registered Perl parser, imports packaging modules, compiles and executes a libelf/OpenSSL host program, and verifies its
Debian shared-library dependencies. A temporary Debian source fixture runs
`dpkg-buildpackage` with `Rules-Requires-Root: no`, including Python packaging,
and verifies native dependencies, debug-symbol splitting and root ownership in
the resulting archives. ARM64 also compiles and links an ARM ELF32
shared object with the declared `gcc-arm-linux-gnueabihf` toolchain, needed by
the kernel's 32-bit compatibility vDSO, and checks its source dependency metadata.
The ARM32 output is inspected, not executed. Fixture packages are discarded when
the test ends. CI repeats configure, foreign and kernel runtime tests in a fresh
Bazel output directory with test-result caching disabled. Its execution receipt
requires the prepared runtime trees, including the native kernel runtime, to
come from the disk cache. The foreign test also checks that the packaged `dash`
and `mawk` ELF files remain intact and their shell aliases work after relocation.
Full kernel/package builds and image integration require validation in their
own consumers; this shared-runtime smoke test does not compile a full kernel.

## Autotools and interface generators

`//tools/build_tools:foreign_runtime` supplies a private native execution bundle
for upstream configure/Make builds: autoreconf, Automake/aclocal, Libtool,
protobuf/gRPC and Thrift generators, Python, Perl (including XML::Simple), and
their shell utilities, libraries and macro data. Use it in a `build_data` edge
of `rules_foreign_cc`, or an explicit `cfg = "exec"` attribute. Its `bin` directory
may be prepended to the action's PATH. Executable targets such as `:autoreconf`,
`:protoc`, `:grpc_cpp_plugin` and `:thrift` are also public.

The protoc bundle includes its matching well-known `.proto` definitions. Its
wrapper appends the private schema directory after explicit caller include
paths, and retains protoc's implicit current-directory lookup when no include
paths were supplied (including through a response file).

The PI and BMv2 source producers need these tools to bootstrap their upstream
Autotools files and generate protocol interfaces. DASH's SAI metadata generation
uses the same declared Perl runtime. Target protobuf, gRPC, Boost and other
libraries remain separate target dependencies. This bundle neither builds Debian
packages nor supplies project runtime files to images.

`foreign_runtime_test` copies the bundle to another directory and bootstraps an
Autotools/Libtool project, generates C++/Python/gRPC interfaces importing
`google.protobuf.Any`/`Empty` and Thrift interfaces, and checks include precedence
and private Python/Perl loading with an empty incoming PATH. Both native
architectures run this test in the declared-build-tools workflow.

`foreign_runtime/bin/dot` uses a private Graphviz plugin index and declared
DejaVu fonts through `GVBINDIR` and a private Fontconfig configuration. Its Perl
runtime includes XML::Simple, Data::Dumper and Getopt::Long for SAI metadata.
Combine `foreign_runtime/bin` with the public `metadata_runtime/bin` to provide
relocatable Doxygen and Aspell to upstream metadata Makefiles. Keep target-object
binutils (such as `nm`) in the selected C/C++ toolchain.

The relocation test also checks both metadata wrappers and renders an SVG with
Graphviz. Graphviz documents the plugin-directory override in its
[command-line environment reference](https://www.graphviz.org/doc/info/command.html#GVBINDIR).

## Autoconf and Libtool compiler environment

Upstream configure adapters can invoke the exported `configure_environment.sh`
with their configure command and arguments. Add the file to `build_data` and pass
its `$(execpath)` in the environment, then run `/bin/sh "$BAZEL_CONFIGURE_ENV"
./configure "$@"`. The adapter retains GCC search flags in the compiler commands, selects `ld`
from the declared GCC distribution
and passes the target compiler's sysroot/include flags to the C and C++
preprocessors. It rejects a linker outside Bazel's execution root.

For large target dependency closures, GCC can exceed Linux's per-string process
limit when it copies all include and library search options into
`COLLECT_GCC_OPTIONS`. The adapter creates a private compiler launcher that
forwards native `-I`, `-iquote` and `-isystem` groups through `-Xpreprocessor`.
Quote-only and system-header search classes remain unchanged, including system
header warning suppression. It preserves those groups' original order and
precedence before already forwarded preprocessor options. It also shortens an
absolute `-L` directory relative to the compiler's physical working directory
when that spelling is shorter. For example, a long Bazel execution-root prefix
can become `../../dependency/lib`. The directory, native library-search priority,
and argument order stay the same. Paths containing `.` or `..` components or
repeated slashes stay unchanged; the launcher does not normalize across symlinks.
Joined and split include/library options are supported.
Invocations containing response files pass through unchanged because
those files may contain additional native include options.

The shared GCC flags use joined `-L<directory>` and `-B<directory>` arguments
because Libtool rejects split `-L` and drops split `-B` options. Startup-object
search paths apply to compiler probes as well as final links. `//tools/build_tools:libtool_test` bootstraps and builds a
real C and C++ shared libraries and an installed consumer with these tools on native AMD64
and ARM64.
The same fixture also compiles and executes C/C++ probes with long search lists,
first confirms the original GCC invocation fails with `Argument list too long`,
and checks competing-header precedence and exact linker/output operands. It
also reproduces the library-only `collect2` spawn failure, then links and runs
successfully with the adapter. Competing static libraries verify search priority
from nested and symlinked working directories, including SDK paths with spaces.
The quote/system regression also keeps the large header and library closures in
the actual Autoconf and recursive Libtool build. This covers the PI SDK used by
the AMD64 gearbox image: its Debian native interfaces supply 132 quote roots and
272 system roots, which the old ordinary-include-only launcher left in GCC's
collected options. In the retained gearbox failure those options reached
136,783 bytes, exceeding Linux's 131,072-byte per-string limit, although no
individual compiler argument exceeded 394 bytes.

## Cross-compile kernel packages on AMD64

Consume `//tools/build_tools:kernel_runtime` through a `cfg = "exec"`
attribute for native or cross kernel builds. On AMD64 this selects one
`kernel_runtime_amd64` tree containing AMD64 tools plus both ARM64 and ARMHF
compilers, headers, libraries and Debian package metadata. All three target
architectures reuse that same tree. On ARM64 it selects the native ARM64
runtime, including its existing ARM32 compatibility compiler. The target
platform for ARMHF is `//platforms:armv7_trixie`.

The shared `build_tools` package set supplies pinned Debian cross compilers;
explicit architecture labels add target libc/headers from `sysroot` and
libelf/OpenSSL dependencies. Debian multiarch directories keep the ABI-specific
files apart inside the same AMD64 tree. Payload merging rejects conflicting
shared files. The private dpkg database records AMD64, ARM64 and ARMHF package
controls, library symbols and file ownership for build-dependency checks and
generated package dependencies.

Make, Python, packaging tools, `host-gcc` and plain `pkgconf` continue using
AMD64 executables and libraries. Each `<target-triplet>-pkg-config` wrapper
selects its own target libraries; each cross compiler selects the corresponding
headers and libraries while preserving GCC builtin header precedence for
freestanding kernel code. The runtime marker records `architecture = "amd64"`
and `cross_architectures = ["arm64", "armhf"]`, and includes all package
archives in one input identity. No target program is executed. The
`kernel_cross_runtime_arm64`, `kernel_cross_runtime_armhf` and
`kernel_build_tools_armhf` compatibility aliases select that same AMD64 tree;
`kernel_build_tools_arm64` aliases the execution-platform `kernel_runtime`.

Run `//tools/build_tools:kernel_cross_runtime_arm64_test` and
`//tools/build_tools:kernel_cross_runtime_armhf_test` on AMD64. Both consume
`kernel_runtime_amd64` and verify native generator execution, target ELF
architecture, hard-float ARMHF user-space and freestanding softfp NEON,
declared library paths, and Debian dependency metadata. The native
`kernel_runtime_test` checks the same tree for AMD64 regressions. Complete
kernel source and package validation belongs to `sonic-linux-kernel`.

## Source-built execution tools

Export source-built host executables with `source_build_tool` from
`//tools/build_tools:defs.bzl`. It uses the same `build_tool` executable and
launcher interface as the DEB tools, with the loader and shared libraries from
the compiler's fixed Trixie sysroot. The binary's existing runfiles are retained.
Dynamic ELF tools use the matching loader; static ELF tools run directly.
Arguments, working directory, streams and exit status are preserved, and ambient
`LD_*` overrides are removed. No Trixie host installation is required.

```starlark
source_build_tool(name = "generator", binary = ":generator_binary")
```

A source tool embedding the fixed APT Python interpreter can select its complete
execution environment explicitly:

```starlark
source_build_tool(
    name = "python_generator",
    binary = ":python_generator_binary",
    runtime = "@sonic_build_infra//tools/build_tools:foreign_runtime",
)
```

The foreign runtime supplies its matching loader, libraries and Python standard
library. The launcher sets `PYTHONHOME` to that runtime and disables user site
packages. A caller's declared `PYTHONPATH` remains available for component-owned
modules. The default compiler-sysroot runtime is unchanged.

`build_tools_runtime` also exposes its original package inputs through the
`packages` output group. Use `filegroup(output_group = "packages", ...)` when
an analysis tool needs to combine existing declared package closures; feed those
files directly to `build_tools_runtime(packages = ...)`. The default output stays
the prepared runtime tree.

Consumers select `cfg = "exec"` and pass the target's `FilesToRunProvider` to
actions. Keep the executable's runfiles path intact when changing directories;
make it absolute without resolving its symlink. Exported tools include
`//tools/build_tools:patchelf`, `:gawk` and `:swig`. Component-local generators
use this same interface. The static runtime bootstrap itself does not need a
loader wrapper. Additional tool-specific libraries belong in the source
binary's declared runtime dependencies.

Infra registers `//tools/build_tools:gawk_toolchain` for gawk's execution
toolchain. tar.bzl 0.10.5-sonic.2 and rules_distroless 0.9.4.sonic.16 consume
the selected executable with its runfiles. Their dependency
`gawk@5.3.2.sonic.1` provides a raw-tool fallback for other consumers;
SONiC roots select the infra registration. These adapters leave compilation
rules unchanged.

The shared execution runtime also exports `sphinx-build` and `makeinfo` for
native documentation generation actions. Their Python/Perl modules, Texinfo XS
modules, Sphinx themes and native libraries come from the same pinned
execution-architecture package set.

Bookworm tools use one shared `bookworm_build_tools` package set, with separate
Perl and instrumentation runtime subsets. This approved suite-specific exception
preserves Perl 5.36 for Bookworm XS generators; the existing Trixie `build_tools`
set supplies Perl 5.40. Both subsets use the execution configuration, and target
debug libraries remain separate.

## Kernel runtime package boundary

`kernel_roots.bzl` fixes the execution package roots consumed by
`kernel_runtime` to the maintained 17a1e555 selection: 63 AMD64 roots and
62 ARM64 roots from the same shared `build_tools` APT set. Each root retains
its declared transitive dependencies, and the existing cross-target ABI data,
controls and native library metadata remain unchanged. The existing
`dpkg_status` rule generates status for that selected subset.

This is a conservative compatibility boundary, not a minimal kernel toolset.
New shared generators do not automatically enter the kernel rootfs; extend
the explicit roots when an actual kernel consumer requires another tool.
For example, DocBook's SGML/man tooling can use the shared package set without
adding its unrelated Jade/TeX/PDF dependencies to the kernel compiler and
metadata runtime. Strict runtime symlink validation remains enabled.

`kernel_runtime_tools_test` reuses the existing relocated runtime test, including
actual C/C++ compilation, native shared/static link inputs, Quilt push/pop,
Perl XML parsing, tool execution and Debian dependency metadata. Its explicit
`--tools-only` mode omits only the function that creates the runtime/debug DEB
fixture. `kernel_runtime_test` retains the original full behavior and remains
the CI check for every branch.

## Source patch and XML tools

The existing `foreign_runtime` also supplies public executable targets `patch`,
`xmllint` and `xsltproc`. They use the same pinned `build_tools` dependency set
and private loader/libraries as the other generator tools. Select each executable
or the runtime bundle with `cfg = "exec"`; no component-specific execution APT
set is needed. XML callers declare their schemas/stylesheets, and the wrappers
disable implicit host XML catalog lookup.

Framework CLI uses these tools to apply its vendored klish normal-diff patches,
validate platform/command XML and run its existing feature XSLT. Target libxml2,
libcurl, Python-embed and cJSON libraries remain target dependencies owned by the
Framework build. They are not compiler sysroot or execution-tool inputs.

`foreign_runtime_test` exercises real patch application and reversal rejection,
valid/invalid XSD input and an XSLT transformation after relocating the runtime
with an empty PATH. Existing native AMD64/ARM64 CI retains that coverage and the
fresh-output-base cache check; local validation is recorded per architecture.

## Locked installer worker packages

`sonic-buildimage` uses an AMD64 Debian Trixie worker for its VS installer
assembly. The worker's Dockerfile requests 39 Debian package names, including
`j2cli=0.3.12b-5` and `squashfs-tools=1:4.6.1-1`. Its separately supplied Docker
CE archive also requires `nftables`. These execution tools use the same
`build_tools` collection in `apt/apt.lock.json` as source generators and kernel builds.

| Public target | Output |
| --- | --- |
| `@sonic_build_infra//tools/build_tools:installer_worker_debs_amd64` | One original `rules_distroless` `:deb` archive for every package in the 40 requested names' AMD64 dependency closure. |
| `@sonic_build_infra//tools/build_tools:installer_worker_manifest_amd64` | JSON metadata for exactly those archives, derived from the same canonical lock. |

The metadata repository follows only locked dependency keys. A requested name
may already be transitive in the shared set, as `make` and `binutils` are. It
does not resolve versions, download packages, or change the ARM64 and ARMHF
selections. The DEB target selects explicit `<package>/amd64:deb` aliases, also
for packages whose Debian architecture is `all`.

The manifest has `schema = 1`, `dependency_set`, `architecture`,
`root_packages`, and `packages`. The `packages` object is keyed by the complete
version-2 lock key. Each row contains `name`, `version`, `architecture`, ordered
`urls`, `sha256`, and the locked `depends_on` keys. The source URLs and hashes
come from the reviewed lock.

Raw archives can share the same basename in distinct external repositories.
Consumers must keep the full Bazel paths while enumerating the DEB target, match each file's SHA256 to one manifest row, and verify the complete
set before staging unique filenames. Do not flatten by basename or infer a
package identity from Bazel's internal repository path. These targets supply
archives; the worker still owns real APT/dpkg installation and its installed
runtime checks.

### Refresh the worker inputs

Add worker package requests to the existing `build_tools` collection in
`apt/requests.json`, then use the shared updater from the repository root:

```sh
python3 apt/lock.py update --bazel /path/to/bazel-8.5.1
python3 apt/lock.py check
bazel test //tools/build_tools:installer_worker_debs_test
```

Review and commit `apt/requests.json` and `apt/apt.lock.json` together. The updater
preserves existing archive identities and native file mappings. The worker
metadata repository and archive targets consume that same lock during ordinary
builds. `MODULE.bazel.lock` remains ignored generated resolution evidence.
## Oversized linker arguments

Linux can reject a large `LDFLAGS` environment entry before the configure shell
starts. Source `configure_linker_flags.sh` in the existing build shell, passing a
declared Python interpreter followed by the configure command and its arguments:

```sh
. "$BAZEL_CONFIGURE_LINKER_FLAGS" "$BAZEL_FOREIGN_TOOLS/bin/python3" \
    /bin/sh "$BAZEL_CONFIGURE_ENV" ./configure "$@"
```

For an existing `rules_foreign_cc` consumer, declare the helper in `build_data`
and source it through `configure_prefix` before `/bin/sh` starts. Calling it
inside `configure.sh` is too late to repair that first process launch:

```starlark
build_data = [
    "@sonic_build_infra//tools/build_tools:configure_linker_flags.sh",
    "@sonic_build_infra//tools/build_tools:foreign_runtime",
    # Retain the consumer's other declared configure inputs.
],
configure_prefix = 'source "$$EXT_BUILD_ROOT$$/$(execpath @sonic_build_infra//tools/build_tools:configure_linker_flags.sh)" "$${BAZEL_FOREIGN_TOOLS}/bin/python3" /bin/sh',
env = {
    "BAZEL_FOREIGN_TOOLS": "$(execpath @sonic_build_infra//tools/build_tools:foreign_runtime)",
    # Retain the consumer's other environment entries.
},
```

The helper uses shell builtins to save `LDFLAGS` and replace the environment entry
before executing Python. It preserves shell-quoted argument boundaries and
order, writing one literal argument per line after `sonic-linker-arguments-v1`
in `.sonic-linker-arguments`. The configure command must invoke the existing
`configure_environment.sh` in that same directory. Its compiler launcher expands
only this private vector before per-directory library-path shortening. Ordinary
GCC response files remain opaque. Arguments containing line breaks are rejected.
`CPPFLAGS`, `CFLAGS` and `CXXFLAGS` remain unchanged; component-specific include
policy stays with the consumer.

The Libtool regression reproduces the failed process launch using at least
160,581 bytes of `LDFLAGS` and 573 library search directories, then links and runs
C and C++ programs from a nested directory using the shared helper. Competing
libraries in paths with spaces verify search priority, while separate cases
check literal empty/quoted/backslash arguments and line-break rejection. The
existing compiler response-file, include-order and recursive Libtool checks
continue to run.

### Verified downloads in source CI

The `Bazel` and `Declared build tools` workflows run in Trixie containers on
GitHub-hosted AMD64 and ARM64 runners. They use the public configuration from
the merged SONiC registry mirror setup. Each workflow verifies the downloaded
file's SHA256 before invoking Bazel, including for its fresh-output cache
check. Package integrity comes from the checksums in the existing package
locks.

See [the source CI configuration](../../ci/README.md) for the pinned downloader
file and the recorded artifacts.
