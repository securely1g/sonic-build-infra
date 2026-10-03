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

## Kernel build runtime

`@sonic_build_infra//tools/build_tools:kernel_runtime` produces one directory
containing the shared `build_tools` package closure, including GCC 14, Debian
packaging programs, Kbuild utilities and their libraries. The packages use the
same dated APT snapshots and integrity checks as the generator tools. No kernel
binary is downloaded or embedded in this runtime.

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

AMD64 source CI runs `prepare_rootfs_test` for metadata, archive boundaries and
directory/symlink preservation through a cache transport that drops empty directories,
then `kernel_runtime_test` in the declared root. The latter imports packaging
modules, compiles and executes a libelf/OpenSSL host program, and verifies its
Debian shared-library dependencies. These tests produce no DEBs. Kernel package
compilation and remote-cache reuse are validated by `sonic-linux-kernel` and its
`sonic-buildimage` consumer. ARM64 kernel compilation is outside this change's
validated scope.
