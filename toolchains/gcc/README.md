# Build native and cross-compiled programs with Debian GCC

SONiC imports pinned Debian GCC packages for native Linux AMD64 and ARM64 builds,
and for building ARM64 programs on AMD64. For example, an AMD64 developer machine
can compile an ARM64 library without installing a system cross compiler or
running ARM64 compiler binaries. Native and cross builds use the same combined
AMD64/ARM64 sysroot as Clang.

| Compiler runs on | Program runs on | Debian compiler version |
| --- | --- | --- |
| AMD64 | AMD64 | `14.2.0-19` |
| ARM64 | ARM64 | `14.2.0-19` |
| AMD64 | ARM64 | `14.2.0-19cross1` |

## Select the target

In a consumer that already depends on `sonic-build-infra`, select the host and
target Trixie platforms. For a native AMD64 build, use `x86_64_trixie` for both;
for native ARM64, use `aarch64_trixie` for both. To cross-compile on AMD64, select
an AMD64 host and ARM64 target:

```sh
bazel build //:my_program \
  --host_platform=@sonic_build_infra//platforms:x86_64_trixie \
  --platforms=@sonic_build_infra//platforms:aarch64_trixie
```

The host platform is where build-time programs execute. The target platform is
where the finished program will run. A generator in a `genrule`'s `tools` list
is built for the host; the generated C or C++ is compiled for ARM64. Rules that
select their own executable tool must put `cfg = "exec"` on that tool attribute.

When a consumer registers several C++ toolchain families, select GCC explicitly:

```sh
bazel build //:my_program \
  --host_platform=@sonic_build_infra//platforms:x86_64_trixie \
  --platforms=@sonic_build_infra//platforms:aarch64_trixie \
  --extra_toolchains=@gcc_toolchains//:host_x86_64_target_aarch64_gcc_14.2.0,@gcc_toolchains//:host_x86_64_gcc_14.2.0
```

Import `gcc_toolchains` from the existing GCC extension before referring to that
repository. The native GCC entry also selects GCC for build-time generators.
For Clang, the [test consumer](../sysroot/tests/consumer/MODULE.bazel) shows
upstream `toolchains_llvm` with the same sysroot. It selects
`--extra_toolchains=@llvm_toolchain//:all` with the same host and target flags.

## Compiler and system libraries

Native compilers use Debian's architecture-specific GCC and G++ packages,
version `14.2.0-19`, with binutils `2.44-3`. The cross compiler uses
`gcc-14-aarch64-linux-gnu` and `g++-14-aarch64-linux-gnu`, version
`14.2.0-19cross1`, with `binutils-aarch64-linux-gnu` version `2.44-3`.
The root [MODULE.bazel](../../MODULE.bazel) selects exact compiler and runtime
package versions through the existing Distroless APT extension.
The selected Debian package payloads are verified and extracted as Bazel inputs;
the compiler is not installed into the build machine's package database.

The [gcc.BUILD](gcc.BUILD) template defines the generated compiler repository's
Bazel targets. [debian.bzl](debian.bzl) renders it with the target architecture's
Debian directory name, such as `aarch64-linux-gnu`. Edit the template to change
those targets; generated `BUILD.bazel` files are marked as generated.

Original Debian files remain under `usr/` in each generated compiler repository.
The `bin/x86_64-linux-gnu-*` or `bin/aarch64-linux-gnu-*` entry points are small
launchers that use the repository's pinned loader and libraries for the machine
running the compiler. The compiler's subprocesses,
including `cc1`, `cc1plus`, the assembler and linker, use the same declared
execution runtime. For example, cross GCC uses its packaged AMD64 GMP library
while linking the program against ARM64 system libraries. Native ARM64 GCC
uses ARM64 compiler executables and ARM64 execution libraries.

System headers, C/C++ development headers, system startup objects and target
libraries come from `@sonic_sysroot//root:sysroot`, the same multiarch directory
used by native GCC and Clang. GCC's own helper files, such as compiler specs and
its sanitizer preinit object, remain in the compiler package. This does not add
or claim sanitizer runtime support. The cross compiler reads the sysroot's `aarch64-linux-gnu`
directories; an AMD64 generator reads the `x86_64-linux-gnu` directories.
The cross-compiler packages do not introduce a second target sysroot.

The launcher bootstrap requires `/bin/sh`; CI uses Debian Trixie. GCC's loader
and libraries are declared Bazel inputs for native and cross builds.
ARM64-to-AMD64 GCC support and adaptation of project-specific build scripts
remain separate work.

The extension's `arch` is the target architecture. `exec_arch` defaults to
`arch` for native declarations. Each declaration supplies `debian_packages`, a
list of pinned `data.tar.xz` package payload labels. The cross declaration sets
`exec_arch = "x86_64"` and `arch = "aarch64"`. GCC and binutils toolchains
constrain execution and target architectures separately; existing native
toolchain target names remain unchanged.

## Declared symbol inspection

Use `@sonic-build-infra//toolchains/binutils:nm` to inspect symbols with the
selected native or cross binutils distribution. Its executable carries the
matching private loader and libraries. Foreign Make rules can list this target
in `tools_deps` and set `NM` to its `$(execpath ...)`; no runner-installed `nm`
is needed. Use the repository alias declared by the consuming module.

The action-based GCC toolchain does not provide a usable legacy `$(NM)` path
in Bazel 8.5.1. The public binutils target follows the same executable interface
as `readelf`, `objcopy`, and `objdump`.

## Validation

The shared-sysroot test directory contains two parts:

- `toolchains/sysroot/tests/` holds validation drivers and consistency checks.
- `toolchains/sysroot/tests/consumer/` is a small independent Bazel module that
  imports this checkout as a dependency. It verifies the interface seen by
  another repository, including execution-platform generators.

On either native AMD64 or ARM64, run:

```sh
python3 toolchains/sysroot/tests/validate.py --artifacts /tmp/native-proof
```

This builds and runs C/C++ programs, a static archive and a shared library with
Debian GCC and Clang. It verifies that both compilers use the same sysroot,
checks the executable and archive architectures, and exchanges their shared
libraries to test C++ exceptions and threads across the compiler boundary.

To validate AMD64-to-ARM64 cross compilation, run on AMD64:

```sh
python3 toolchains/sysroot/tests/validate_cross.py build --artifacts /tmp/cross-proof
```

The test builds an AMD64 source generator, then ARM64 C and C++ executables,
a static archive and a shared library with GCC and Clang. It checks the action
graph, architecture-specific headers, ELF architecture, dynamic loader, generated
source and shared sysroot. It retains each compiler's output before switching
compilers and records hashes and generated Bazel resolution state.

Both native and cross GCC checks compare original compiler executables and runtime libraries
with their pinned Debian package payloads. It checks the loader's resolved
libraries, observes the public GCC launcher's runtime, and verifies that
compilation, linking and archiving actions declare the files they need. They
also run the public `readelf` and `objcopy` targets and the position-independent
library check, verifying that these tool interfaces carry their runtime files.
`debian-compiler-validation.json` records the package archives and verified
file hashes alongside the build evidence.

CI transfers those exact outputs and their declared ARM64 runtime libraries to
a native ARM64 runner:

```sh
python3 toolchains/sysroot/tests/validate_cross.py run \
  --bundle /tmp/cross-proof/runtime-bundle.tar.gz --artifacts /tmp/runtime-proof
```

That job runs the C and C++ programs, including threads and exceptions across
a shared-library boundary. It also runs GCC's caller with Clang's library and
the reverse. CI also compares the combined sysroot contents produced on native
AMD64 and ARM64. These validation paths do not produce Debian packages.
