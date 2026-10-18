"""Check Debian filenames without executing the package assembly action."""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")

def _filename_test_impl(ctx):
    env = analysistest.begin(ctx)
    files = analysistest.target_under_test(env)[DefaultInfo].files.to_list()
    asserts.equals(env, 1, len(files))
    if len(files) == 1:
        asserts.true(env, files[0].basename.startswith("sonic-example_1.0-1_"), files[0].basename)
        asserts.false(env, ":" in files[0].basename, files[0].basename)
    return analysistest.end(env)

filename_test = analysistest.make(_filename_test_impl)
