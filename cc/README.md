`shared_library_cc_info` exposes a native `cc_shared_library` to consumers such
as Go cgo and Rust `link_deps` that require `CcInfo`. It preserves the native
producer's library paths and runfiles, including its transitive shared-library
dependencies. It does not copy the ELF or create another `cc_import`.

```starlark
load("@rules_cc//cc:defs.bzl", "cc_library", "cc_shared_library")
load("@sonic_build_infra//cc:shared_library.bzl", "shared_library_cc_info")

cc_library(
    name = "implementation",
    srcs = ["example.cc"],
    hdrs = ["example.h"],
)

cc_shared_library(
    name = "shared",
    shared_lib_name = "libexample.so.0",
    user_link_flags = ["-Wl,-soname,libexample.so.0"],
    deps = [":implementation"],
)

shared_library_cc_info(
    name = "shared_link",
    shared_library = ":shared",
)

cc_library(
    name = "api",
    hdrs = ["example.h"],
    deps = [":shared_link"],
)
```

Use `:api` in ordinary C++, Go, or Rust native dependency attributes. Use
`:shared` in another native `cc_shared_library`'s `dynamic_deps`. Native shared
libraries whole-archive their direct `deps`; ordinary consumers of
`:implementation` retain their usual static-link behavior. Do not merge
`:implementation`'s linking context into the adapter, which would add its
static archive alongside the shared library.

The adapter forwards the native producer's `linker_input`, which already
includes imported precompiled dynamic libraries, and the linker inputs from
its transitive `dynamic_deps`. Header and compilation settings belong on the
surrounding `cc_library`; they are intentionally separate from this link-only
adapter.

Prebuilt distro libraries still need a runtime environment that can locate their
own dependencies. For example, the imported ZeroMQ library has no RUNPATH for
its sodium, PGM, and NORM dependencies. Forwarding its native link information
does not add those paths to that ELF. Installed system paths or a declared
runtime environment must satisfy that requirement. The adapter does not change
the GCC toolchain's default RUNPATH behavior.

`bazel test //cc/tests:transitive_runtime_test //cc/tests:shared_library_layout_test`
checks an executable that calls a middle shared library backed by a separate
leaf shared library. Both libraries are native producers. These targets use
the default linker behavior, without flags that force RUNPATH. The ELF check
requires RUNPATH, rejects RPATH, and verifies that the executable only needs
the middle library directly. The standard native AMD64 and ARM64 CI runs both
tests.

`native_runtime_binary` and `native_runtime_test` in `//cc:runtime_test.bzl`
execute Linux target ELFs using the matching loader and libraries from
explicit target Debian payload tar archives. They preserve installed PT_INTERP.
Provide `binary` and `runtime_payloads` (including libc6 and the runtime library
closure). Payload labels may be architecture-selecting filegroups. The wrapper
retains the binary's runfiles and its relative ORIGIN library paths.
Shared libraries expand ORIGIN relative to their own location. RPATH search
paths reach indirect dependencies; RUNPATH applies only to direct dependencies.
An execution tool attribute with `cfg = "exec"` selects the complete wrapper
in exec configuration.
The target platform's x86_64 or aarch64 constraint selects the expected ELF
machine. The binary and every payload ELF must match it; preparation never
infers the target CPU from its own interpreter. Cross execution still requires
an external executor that can run the real target loader and binary. This
interface supports library payloads, not a chroot filesystem or arbitrary
package data requirements.

ELF parsing and payload extraction run through the declared
`//tools/build_tools:python3` in exec configuration, with its complete private
runtime in runfiles. The initial SDK bootstrap snapshots the caller environment
before starting that native preparation tool. Preparation receives sanitized
Python/loader startup variables; an unlinked temporary file transfers the
original environment by an inherited descriptor, which is closed before the
target or instrument starts. Unset and empty values remain distinct. The
target retains caller Python settings and the existing filtering of
`LD_LIBRARY_PATH`, `LD_PRELOAD`, and `LD_AUDIT`; the private preparation
`PYTHONHOME` never replaces a target Python runtime. Other Python tests and
their interpreter selection are unchanged.

The final subprocess explicitly retains the caller's working directory. This
avoids the native Python 3.13 `posix_spawn` path that returned `EFAULT` for large
environments under traced execution, without changing arguments, relative
paths, or the target environment. The focused `preparation_test` covers the
target CPU checks, caller environment, anonymous descriptor lifetime, startup
failures, signals, and relative paths. Its synthetic ELF and interpreter
stand-ins supplement the real runtime contract tests; they do not prove cross
execution on their own.

Set `instrument` to a declared executable (selected in exec configuration) and
`instrument_args` to its arguments when testing through Valgrind or a similar
ELF instrument. The instrument receives the matching loader, its library search
arguments, the real target ELF, and the program arguments. Its runfiles are
included. Both the program exit and an instrument-reported error propagate to
the caller. The runtime regression uses real Memcheck on valid and out-of-bounds
memory accesses to verify this boundary.
When an instrument needs a stripped library's internal symbols, include its
matching debug package in `runtime_payloads`. Matching GNU Build-ID and
debuglink CRC data are installed next to the declared library, so debug lookup
continues to work after runtime relocation.

The runtime regression executes real target glibc 2.41 programs, checks argument
and nonzero exit forwarding, preserves signal termination for callers using
waitpid/WIFSIGNALED, exercises the wrapper as an exec tool and rejects a
missing declared libc6 payload. It addresses the observed failure where the
system interpreter started a binary with imported target glibc and crashed
before main.
