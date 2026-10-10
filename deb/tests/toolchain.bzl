"""Expose the selected upstream executable and runfiles to extraction tests."""

def _test_bsdtar_impl(ctx):
    toolchain = ctx.toolchains["@tar.bzl//tar/toolchain:type"]
    return [DefaultInfo(
        files = depset([toolchain.tarinfo.binary]),
        runfiles = ctx.runfiles(transitive_files = toolchain.default.files),
    )]

test_bsdtar = rule(
    implementation = _test_bsdtar_impl,
    toolchains = ["@tar.bzl//tar/toolchain:type"],
)
