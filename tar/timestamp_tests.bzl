"""Small fixtures for archive timestamp regressions."""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts", "unittest")
load("@tar.bzl//tar:tar.bzl", "tar_lib")
load(":sonic_tar.bzl", "timestamped_mtree")

def _timestamp_defaults_test_impl(ctx):
    env = unittest.begin(ctx)
    unchanged = ["", "  # comment", "#mtree", "file type=file time=0", "dir\ttype=dir\ttime=123"]
    asserts.equals(env, unchanged, timestamped_mtree(unchanged))
    entries = [
        "escaped\\040name\ttype=file content=$(location :input)",
        "link type=link link=escaped\\040name",
        "dir type=dir",
        "time=filename type=file content=$(location :time=input)",
    ]
    asserts.equals(env, [
        entries[0] + " time=1672560000",
        entries[1] + " time=0",
        entries[2] + " time=1672560000",
        entries[3] + " time=1672560000",
    ], timestamped_mtree(entries))
    return unittest.end(env)

timestamp_defaults_test = unittest.make(_timestamp_defaults_test_impl)

def _rejected_manifest_impl(ctx):
    timestamped_mtree(ctx.attr.entries)
    return [DefaultInfo()]

_rejected_manifest = rule(
    implementation = _rejected_manifest_impl,
    attrs = {"entries": attr.string_list()},
)

def _timestamp_rejection_test_impl(ctx):
    env = analysistest.begin(ctx)
    asserts.expect_failure(env, "sonic_tar")
    return analysistest.end(env)

_timestamp_rejection_test = analysistest.make(_timestamp_rejection_test_impl, expect_failure = True)

def timestamp_rejection_tests():
    """Ensure unsupported stateful/continued syntax fails instead of drifting."""
    for name, entry in {
        "set": "/set time=123",
        "unset": "/unset time",
        "parent": "..",
        "continued": "file type=file \\",
    }.items():
        _rejected_manifest(
            name = "rejected_" + name,
            entries = [entry],
            tags = ["manual"],
        )
        _timestamp_rejection_test(
            name = "timestamp_" + name + "_test",
            target_under_test = ":rejected_" + name,
        )

def _bsdtar_test_tool_impl(ctx):
    toolchain = ctx.toolchains[tar_lib.toolchain_type]
    runfiles = ctx.runfiles(transitive_files = toolchain.default.files)
    if toolchain.default.default_runfiles != None:
        runfiles = runfiles.merge(toolchain.default.default_runfiles)
    return [DefaultInfo(
        files = depset([toolchain.tarinfo.binary]),
        runfiles = runfiles,
    )]

# Exercise the same native tool selected by tar.bzl, rather than host /usr/bin/tar.
bsdtar_test_tool = rule(
    implementation = _bsdtar_test_tool_impl,
    toolchains = [tar_lib.toolchain_type],
)
