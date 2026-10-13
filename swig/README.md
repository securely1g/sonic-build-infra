# SWIG feature definitions

Both `swig_gen` (Python) and `swig_gen_go` accept `defines`, a list of
preprocessor definitions without the `-D` prefix. For example,
`defines = ["ENABLE_YANG_MODULES", "FEATURE_LEVEL=2"]` passes those two
flags directly to SWIG. Use `select()` when a Bazel build setting controls
the interface. The default empty list preserves the existing interface.

These flags affect SWIG parsing only. Set the corresponding C++ compilation
flags on the generated wrapper's library target when its headers require them.
The rules retain their existing language options, word size and SWIG library
inputs.

`//swig/tests:defines_test` generates real Python and Go wrappers with and
without a feature. It checks that both a bare definition and a definition
with a value select the expected interface, and that the unselected interface
is absent. Source CI runs it on native AMD64 and ARM64.

The definitions API is adapted from Song Qing's
[sonic_rules change](https://github.com/songqing-sq/sonic_rules/commit/bfd0f95831074ce67fb25bbe6394b55d607bce34).
