# SWIG generation

`swig_gen` (Python) and `swig_gen_go` are exported from `swig/defs.bzl`.
Both accept `wordsize = 32` or `wordsize = 64` and a `defines` list such as
`["FEATURE", "VALUE=7"]`. These describe the target, while the SWIG executable
continues to run in the execution configuration.

The default is 64 bits and no extra defines, preserving existing generation.
64-bit generation defines `SWIGWORDSIZE64`; 32-bit generation omits it.
Go receives the matching `-intgosize`. The caller should use `wordsize` to
select target width, without adding `SWIGWORDSIZE64` to `defines` itself.

Existing direct headers, transitive `CcInfo` inputs, SWIG library tree/file
inputs, output attributes, `DefaultInfo`, and named output groups are unchanged.
Architecture selection and binding declarations belong to the consumer.
