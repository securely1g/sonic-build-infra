# Declared build-time tools

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
and native Perl module loading. It also compiles and
runs a small C program linked to a library built by `configure_make_test`, proving the registered
toolchains work when foreign_cc stages their runtime tree into an action.
CI then repeats the tool runtime test in a fresh Bazel output directory, with
compilation results shared only through the disk cache. It checks the execution
log reports `PrepareBuildTools` as a disk-cache hit and retains that receipt.

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
same dated APT snapshots and integrity checks as the generator tools. No kernel
binary is downloaded or embedded in this runtime.

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

The `sonic-linux-kernel` source-build rule selects this target with
`attr.label(allow_single_file = True, cfg = "exec")`. It copies the tree to
private scratch and enters it with `chroot`, allowing Debian's scripts to use
their normal `/usr/bin`, Perl and Python paths without reading tools from the
worker. This execution path requires root with `CAP_SYS_CHROOT` and `CAP_MKNOD`
inside a disposable Linux worker. The cacheable runtime itself contains no
device nodes and is prepared without root, network access or package maintainer
script execution. Its `kernel-runtime.json` records the architecture, package
versions, input hashes and a path-independent identity.

Empty directories contain a zero-byte `.bazel-keep-directory` file so Bazel's
remote-cache downloader preserves them, including targets of directory symlinks.

The preparation action supplies Debian's merged `/usr` layout and explicit
build-command alternatives. It retains package status and library ownership,
shlibs and symbols metadata so `dpkg-shlibdeps` can calculate the source-built
kernel tools' dependencies. The compiler's execution-side libc headers remain
part of this runtime; they do not replace any consuming target's sysroot.

Native AMD64 and ARM64 source CI run `prepare_rootfs_test` for metadata, archive
boundaries and directory/symlink preservation through a cache transport that drops
empty directories,
then `kernel_runtime_test` in the declared root. The latter imports packaging
modules, compiles and executes a libelf/OpenSSL host program, and verifies its
Debian shared-library dependencies. ARM64 also compiles and links an ARM ELF32
shared object with the declared `gcc-arm-linux-gnueabihf` toolchain, needed by
the kernel's 32-bit compatibility vDSO, and checks its source dependency metadata.
The ARM32 output is inspected, not executed. These tests produce no DEBs. Kernel
package compilation and remote-cache reuse are validated by `sonic-linux-kernel` and its
`sonic-buildimage` consumer. This shared-runtime smoke test does not compile a
full kernel.

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
forwards the native `-I` group through `-Xpreprocessor`. It preserves that group's
precedence before already forwarded preprocessor options and leaves linker
arguments and their order unchanged. Joined and split include options are
supported. Invocations containing response files pass through unchanged because
those files may contain additional native include options.

The shared GCC flags use joined `-L<directory>` and `-B<directory>` arguments
because Libtool rejects split `-L` and drops split `-B` options. Startup-object
search paths apply to compiler probes as well as final links. `//tools/build_tools:libtool_test` bootstraps and builds a
real C and C++ shared libraries and an installed consumer with these tools on native AMD64
and ARM64.
The same fixture also compiles and executes C/C++ probes with long search lists,
first confirms the original GCC invocation fails with `Argument list too long`,
and checks competing-header precedence and exact linker/output operands.
