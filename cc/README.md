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

`bazel test //cc/tests:transitive_runtime_test //cc/tests:shared_library_layout_test`
checks an executable that calls a middle shared library backed by a separate
leaf shared library. Both libraries are native producers. The ELF check requires
RUNPATH, rejects RPATH, and verifies that the executable only needs the middle
library directly. The standard native AMD64 and ARM64 CI runs both tests.
