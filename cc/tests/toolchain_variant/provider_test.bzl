"""Validate the providers consumed by native linking and debug collection."""

load("@rules_cc//cc:defs.bzl", "CcInfo")
load("@rules_cc//cc/common:cc_shared_library_info.bzl", "CcSharedLibraryInfo")
load("//binary:debug_symbols.bzl", "DebugSymbolsInfo")

def _debug_fixture_impl(ctx):
    actual = ctx.attr.actual
    groups = {}
    if OutputGroupInfo in actual:
        for name in dir(actual[OutputGroupInfo]):
            value = getattr(actual[OutputGroupInfo], name)
            if type(value) == "depset":
                groups[name] = value
    groups["fixture_symbols"] = depset([ctx.file.symbols])
    return [actual[p] for p in [DefaultInfo, CcSharedLibraryInfo] if p in actual] + [
        OutputGroupInfo(**groups),
        DebugSymbolsInfo(symbols = depset([ctx.file.symbols])),
    ]

debug_fixture = rule(
    implementation = _debug_fixture_impl,
    attrs = {"actual": attr.label(), "symbols": attr.label(allow_single_file = True)},
)

def _providers_test_impl(ctx):
    original = ctx.attr.original
    selected = ctx.attr.selected
    for provider in [DefaultInfo, CcInfo, CcSharedLibraryInfo, OutputGroupInfo, DebugSymbolsInfo]:
        if (provider in original) != (provider in selected):
            fail("toolchain boundary lost provider: " + str(provider))
    if original[DefaultInfo].files.to_list() != selected[DefaultInfo].files.to_list():
        fail("toolchain boundary changed default artifacts")
    if CcInfo in original:
        if original[CcInfo].compilation_context.headers.to_list() != selected[CcInfo].compilation_context.headers.to_list():
            fail("toolchain boundary changed public headers")
    if CcSharedLibraryInfo in original:
        if original[CcSharedLibraryInfo].linker_input != selected[CcSharedLibraryInfo].linker_input:
            fail("toolchain boundary changed shared linking input")
    if OutputGroupInfo in original:
        for group in dir(original[OutputGroupInfo]):
            if not group.startswith("_") and type(getattr(original[OutputGroupInfo], group)) == "depset":
                if getattr(original[OutputGroupInfo], group).to_list() != getattr(selected[OutputGroupInfo], group).to_list():
                    fail("toolchain boundary changed output group: " + group)
    if DebugSymbolsInfo in original:
        if original[DebugSymbolsInfo].symbols.to_list() != selected[DebugSymbolsInfo].symbols.to_list():
            fail("toolchain boundary changed symbol artifacts")
    out = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(out, "#!/bin/sh\nexit 0\n", is_executable = True)
    return [DefaultInfo(executable = out)]

providers_test = rule(
    implementation = _providers_test_impl,
    test = True,
    attrs = {"original": attr.label(), "selected": attr.label()},
)
