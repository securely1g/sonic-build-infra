"""Preprocess a C input with its target toolchain and declared CcInfo dependencies."""

load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:defs.bzl", "CcInfo", "cc_common")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain", "use_cc_toolchain")

def _impl(ctx):
    cc = find_cc_toolchain(ctx)
    features = cc_common.configure_features(ctx = ctx, cc_toolchain = cc, requested_features = ctx.features, unsupported_features = ctx.disabled_features)
    variables = cc_common.create_compile_variables(feature_configuration = features, cc_toolchain = cc)
    flags = cc_common.get_memory_inefficient_command_line(feature_configuration = features, action_name = ACTION_NAMES.c_compile, variables = variables)
    compilation = cc_common.merge_cc_infos(cc_infos = [dep[CcInfo] for dep in ctx.attr.deps]).compilation_context
    output = ctx.actions.declare_file(ctx.label.name + ".i")
    args = ctx.actions.args()
    args.add_all(flags)
    args.add_all(["-E", "-P", "-x", "c"])
    args.add_all(compilation.includes, format_each = "-I%s")
    args.add_all(compilation.quote_includes, format_each = "-iquote%s")
    args.add_all(compilation.system_includes, format_each = "-isystem%s")
    args.add_all(compilation.external_includes, format_each = "-isystem%s")
    args.add_all(compilation.defines, format_each = "-D%s")
    args.add_all(ctx.attr.copts)
    args.add(ctx.file.src)
    args.add("-o")
    args.add(output)
    ctx.actions.run(
        executable = cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.c_compile),
        arguments = [args],
        inputs = depset([ctx.file.src], transitive = [cc.all_files, compilation.headers]),
        outputs = [output],
        mnemonic = "CcPreprocess",
    )
    return [DefaultInfo(files = depset([output]))]

cc_preprocess = rule(
    implementation = _impl,
    attrs = {
        "src": attr.label(mandatory = True, allow_single_file = True),
        "deps": attr.label_list(providers = [CcInfo]),
        "copts": attr.string_list(),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)
