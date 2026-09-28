"""Check the installed runtime paths emitted by the selected C/C++ toolchain."""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")

def _installed_runtime_paths_test_impl(ctx):
    env = analysistest.begin(ctx)
    links = [
        action
        for action in analysistest.target_actions(env)
        if action.mnemonic == "CppLink"
    ]
    asserts.equals(env, 1, len(links), "expected one C/C++ link action")
    if len(links) == 1:
        expected_count = 1 if ctx.attr.enabled else 0
        for path in ctx.attr.paths:
            arg = "-Wl,-rpath=" + path
            actual_count = len([value for value in links[0].argv if value == arg])
            asserts.equals(env, expected_count, actual_count, arg)

    return analysistest.end(env)

installed_runtime_paths_test = analysistest.make(
    _installed_runtime_paths_test_impl,
    attrs = {
        "enabled": attr.bool(default = True),
        "paths": attr.string_list(mandatory = True),
    },
)
