"""Model an APT provider's ordered flags and declared indirect DSOs in a fixture.

Native cc_library.linkopts deduplicates repeated tokens before shared-library
linking. APT instead carries its ordered arguments on a library-bearing CcInfo
input. This fixture exercises that public contract without an APT dependency.
"""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")
load("@rules_cc//cc:defs.bzl", "CcInfo", "cc_common")

def _runtime_inputs_impl(ctx):
    info = ctx.attr.dep[CcInfo]
    inputs = []
    for item in info.linking_context.linker_inputs.to_list():
        inputs.append(cc_common.create_linker_input(
            owner = item.owner,
            libraries = depset(item.libraries),
            user_link_flags = item.user_link_flags + [ctx.expand_make_variables("linkopts", flag, {}) for flag in ctx.attr.linkopts],
            additional_inputs = depset(item.additional_inputs + ctx.files.runtime_files),
        ))
    return [
        ctx.attr.dep[DefaultInfo],
        CcInfo(
            compilation_context = info.compilation_context,
            linking_context = cc_common.create_linking_context(linker_inputs = depset(inputs)),
        ),
    ]

runtime_inputs = rule(
    implementation = _runtime_inputs_impl,
    attrs = {
        "dep": attr.label(mandatory = True, providers = [CcInfo]),
        "runtime_files": attr.label_list(allow_files = True),
        "linkopts": attr.string_list(),
    },
    provides = [CcInfo],
)

def _paired_flags_test_impl(ctx):
    """Keep every driver-bypass pair, even when native linking can mask its loss."""
    env = analysistest.begin(ctx)
    target = analysistest.target_under_test(env)
    directory = target[DefaultInfo].files.to_list()[0].dirname
    expected = [
        "-Xlinker", "-L" + directory + "/paired,first",
        "-Xlinker", "-L" + directory + "/paired,second",
        "-Xlinker", "-rpath-link=" + directory + "/paired,first",
        "-Xlinker", "-rpath-link=" + directory + "/paired,second",
    ]
    actual = []
    for item in target[CcInfo].linking_context.linker_inputs.to_list():
        actual.extend(item.user_link_flags)
    asserts.equals(env, expected, actual[-len(expected):])
    return analysistest.end(env)

paired_flags_test = analysistest.make(_paired_flags_test_impl)
