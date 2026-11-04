"""Expose only direct linker inputs while preserving producer compilation contexts.

Use before cc_static_library when an installed archive contains selected direct
objects while its transitive libraries are supplied separately by consumers.
"""

load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

def _impl(ctx):
    # cc_static_library includes transitive objects; keep only the selected
    # producers' own linker inputs without rebuilding or copying their artifacts.
    owners = {dep.label: True for dep in ctx.attr.deps}
    inputs = []
    found = {}
    for dep in ctx.attr.deps:
        for linker_input in dep[CcInfo].linking_context.linker_inputs.to_list():
            if linker_input.owner in owners:
                inputs.append(linker_input)
                found[linker_input.owner] = True
    missing = [owner for owner in owners if owner not in found]
    if missing:
        fail("Missing direct archive objects for %s" % missing)
    compilation = cc_common.merge_cc_infos(cc_infos = [dep[CcInfo] for dep in ctx.attr.deps])
    return [CcInfo(
        compilation_context = compilation.compilation_context,
        linking_context = cc_common.create_linking_context(linker_inputs = depset(inputs)),
    )]

direct_linker_inputs = rule(
    implementation = _impl,
    attrs = {"deps": attr.label_list(providers = [CcInfo], mandatory = True)},
)
