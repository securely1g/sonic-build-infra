# Shared API consumer regression fixture

Run from this directory with Bazel 8.5.1. It loads the public rules from a
separate module with a local override, exercising their repository mappings.

```sh
bazel test //:all
bazel query @unsafe//:files
```

The first command must pass all nine tests. The second must fail with
`The Debian sysroot contains unexpected absolute symlinks`.
The fixture requires host `tar`, `xz`, `ln`, and `find`, like the assembler.
Use the repository's configured SONiC registry before BCR for dependencies.

The SWIG tests inspect analysis actions: both languages, 32-bit and default
64-bit, tree and file libraries, explicit defines, execution configuration,
transitive headers, and the existing output groups. Consumer compilation and
runtime checks remain the responsibility of component validation.
