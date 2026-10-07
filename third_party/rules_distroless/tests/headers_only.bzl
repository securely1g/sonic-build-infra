"""Expose an imported package's compilation context without runtime libraries."""

load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

def _headers_only_impl(ctx):
    return [CcInfo(compilation_context = ctx.attr.dep[CcInfo].compilation_context)]

headers_only = rule(
    implementation = _headers_only_impl,
    attrs = {"dep": attr.label(mandatory = True, providers = [CcInfo])},
)
