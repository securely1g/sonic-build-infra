"""Select registered toolchains within one C/C++ dependency subtree."""

load("@rules_cc//cc:defs.bzl", "CcInfo")
load("@rules_cc//cc/common:cc_shared_library_info.bzl", "CcSharedLibraryInfo")
load("@rules_cc//cc/common:cc_shared_library_hint_info.bzl", "CcSharedLibraryHintInfo")
load("//binary:debug_symbols.bzl", "DebugSymbolsInfo")

def _toolchain_transition_impl(settings, attr):
    selected = [str(label) for label in attr.extra_toolchains]
    return {
        "//command_line_option:extra_toolchains": selected + [
            label
            for label in settings["//command_line_option:extra_toolchains"]
            if label not in selected
        ],
    }

_toolchain_transition = transition(
    implementation = _toolchain_transition_impl,
    inputs = ["//command_line_option:extra_toolchains"],
    outputs = ["//command_line_option:extra_toolchains"],
)

def _forwarded_providers(ctx, actual):
    return [
        actual[provider]
        for provider in [CcInfo, CcSharedLibraryInfo, OutputGroupInfo, DebugSymbolsInfo]
        if provider in actual
    ] + [CcSharedLibraryHintInfo(attributes = ["actual"], owners = [ctx.label])]

def _cc_toolchain_variant_impl(ctx):
    actual = ctx.attr.actual[0]
    default = actual[DefaultInfo]
    # Native filegroups set files_to_run.executable for a single data artifact.
    # Library/packaging consumers retain those files and runfiles; executable
    # consumers use the binary interface, which creates its own executable.
    return [DefaultInfo(
        files = default.files,
        default_runfiles = default.default_runfiles,
        data_runfiles = default.data_runfiles,
    )] + _forwarded_providers(ctx, actual)

def _cc_binary_toolchain_variant_impl(ctx):
    actual = ctx.attr.actual[0]
    default = actual[DefaultInfo]
    binary = default.files_to_run.executable
    if binary == None:
        fail("cc_binary_toolchain_variant requires an executable input")
    executable = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.symlink(output = executable, target_file = binary, is_executable = True)
    return [DefaultInfo(
        executable = executable,
        files = depset([executable] + [file for file in default.files.to_list() if file != binary]),
        default_runfiles = default.default_runfiles.merge(ctx.runfiles(files = [binary])),
        data_runfiles = default.data_runfiles,
    )] + _forwarded_providers(ctx, actual)

_VARIANT_ATTRS = {
    "actual": attr.label(mandatory = True, cfg = _toolchain_transition),
    "extra_toolchains": attr.label_list(
        doc = "Registered toolchain targets, ordered ahead of inherited extra toolchains.",
    ),
    "_allowlist_function_transition": attr.label(
        default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
    ),
}

cc_toolchain_variant = rule(
    implementation = _cc_toolchain_variant_impl,
    attrs = _VARIANT_ATTRS,
    provides = [CcSharedLibraryHintInfo],
    doc = """Forward library and packaging providers across a local toolchain selection.

The caller declares registered toolchain targets. Target/host platforms and other
build options retain their original values; siblings use their original tools.
""",
)

cc_binary_toolchain_variant = rule(
    implementation = _cc_binary_toolchain_variant_impl,
    attrs = _VARIANT_ATTRS,
    provides = [CcSharedLibraryHintInfo],
    executable = True,
    doc = "Forward an executable and its runfiles through a local toolchain selection.",
)
