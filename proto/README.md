# Pinned protobuf build tool

`//proto:protoc` runs Debian Trixie's protobuf 3.21.12 compiler using the
shared `build_tools` package set. Its declared payloads include the compiler,
loader, runtime libraries, and well-known proto imports. It does not use a
compiler or protobuf installation from the host.

Consumer rules must select the executable using `cfg = "exec"` and
`executable = True`, and pass its `DefaultInfo.files_to_run` as an action tool.
This selects the native AMD64 or ARM64 compiler for the execution platform;
target protobuf headers and libraries remain separate target dependencies.

The launcher verifies its declared dynamic-library closure and compiler
version before invoking the generator. The shared Debian source snapshot and
Bazel lockfile select exact packages; no separate package downloader or lock
is maintained here. The protobuf registry module owns language-specific
source-generation rules and matching target library declarations.
