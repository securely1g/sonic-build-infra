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

## Debian package metadata

Use `:dpkg-gencontrol`, `:dpkg-shlibdeps` and `:dpkg-gensymbols` when a source producer needs Debian
package metadata. For example, the Rust supervisor listener owns its package
name and description in `debian/control`; `dpkg-shlibdeps` derives the minimum
library versions from its compiled executable, and `dpkg-gencontrol` combines
those dependencies with the original control and changelog files. A source
library can export its symbols with `dpkg-gensymbols` using that library's ELF
and original changelog version.

These tools produce text metadata only. They do not create a `.deb`, compile the
program again, or mark any package as installed. The public executable targets
use the same execution configuration and `FilesToRunProvider` pattern shown
above. `:metadata_runtime` exposes their shared tree when a generator needs to
invoke several of its tools.

The existing `build_tools` APT set supplies pinned dpkg-dev, binutils, Perl and
all runtime dependencies. The metadata profile uses its own Perl modules,
architecture data, dynamic loader and objdump. It starts with an empty package
database and no default host library search directories. Pass the actual target
libraries with `-l<directory>` and their original `DEBIAN/shlibs` or
`DEBIAN/symbols` through package build directories (`-S<directory>`). A producer
can instead pass its own generated shlibs file with `-L<file>`. Explicit package
metadata must be a declared input; libraries alone cannot tell dpkg their
Debian package names or version policy. Do not copy dependencies from an older
image's installed package record.

`DPKG_ADMINDIR` can select a declared package database when one is needed. The
caller must set `DEB_HOST_ARCH` when generating metadata for an architecture
different from the native execution architecture. Native AMD64 and ARM64 are
the tested configurations; this profile does not supply cross objdump tools.

`//tools/build_tools:metadata_runtime_test` compiles a small program, derives its
libc dependency from the matching pinned package's symbols file, then generates
the final binary control paragraph. Removing the library or its dependency
metadata must fail even when the host has libc installed. The test also checks
that Perl modules and dpkg data are loaded from the declared runtime. The
existing native build-tools workflow runs this test on AMD64 and ARM64.

## Generate Debian installation scripts

The `dh_install`, `dh_installdeb`, `dh_installdebconf`, `dh_makeshlibs`, `dh_perl` and
`dh_installsystemduser`, `dh_installtmpfiles` and `dh_python3` targets run Debian's original debhelper programs from the
same pinned snapshot as the other execution tools. Their separate
`debian_runtime` includes Perl, debhelper data, translated-template tools and
private subprocesses. Python helpers also use the pinned Debian Python 3.13
interpreter, package-name mappings and autoscripts. A small bootstrap changes
only dh-python's fixed ABI-query, mapping and dpkg paths. Dependency metadata
remains enabled; package searches use the private dpkg database. Installed shebangs and package paths retain their Debian values. The launcher uses the original tools' `DH_DATAFILES`,
`DH_AUTOSCRIPTDIR` and `PODEBCONF_*` configuration instead of copying their logic.

A source producer stages its compiled payload under `debian/<package>` in a
private action directory, copies its original `debian/control`, `changelog` and
maintainer inputs, then runs the relevant commands before `dpkg-gencontrol`.
For example, iproute2 needs `dh_installdebconf` followed by `dh_installdeb`: this
keeps its translated configuration question, debconf dependency, removal of
obsolete conffiles and purge script. `dh_python3` supplies bytecode setup/removal scripts and derives
Python dependencies from the actual module/extension payload. Shared-library producers use
`dh_makeshlibs` to generate the normal `ldconfig` trigger and library metadata.
The commands do not run package installation scripts or create a DEB.

`debian_runtime_test` checks these behaviors with an empty caller PATH on native
AMD64 and ARM64. Source producers remain responsible for selecting the original
helper order/options, packaging the resulting controls, and checking actual
installation and removal of their resulting DEBs.
