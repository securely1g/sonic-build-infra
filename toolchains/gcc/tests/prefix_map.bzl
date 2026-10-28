"""Compile an absolute source path and inspect its observable DWARF."""
load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:defs.bzl", "cc_common")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain", "use_cc_toolchain")

def _impl(ctx):
    cc = find_cc_toolchain(ctx)
    configuration = cc_common.configure_features(ctx = ctx, cc_toolchain = cc, requested_features = ctx.features, unsupported_features = ctx.disabled_features)
    compiler = cc_common.get_tool_for_action(feature_configuration = configuration, action_name = ACTION_NAMES.c_compile)
    obj = ctx.actions.declare_file(ctx.label.name + ".o")
    dwarf = ctx.actions.declare_file(ctx.label.name + ".dwarf")
    ctx.actions.run_shell(command = '"{compiler}" -g -c "$PWD/{source}" -o "{output}"\n"{objdump}" --dwarf=info "{output}" > "{dwarf}"'.format(compiler = compiler, source = ctx.file.src.path, output = obj.path, objdump = compiler.removesuffix("gcc") + "objdump", dwarf = dwarf.path), inputs = depset([ctx.file.src], transitive = [cc.all_files]), outputs = [obj, dwarf], mnemonic = "GccPrefixMapContract")
    return [DefaultInfo(files = depset([obj, dwarf]))]
prefix_map_fixture = rule(implementation = _impl, attrs = {"src": attr.label(allow_single_file = True, mandatory = True)}, fragments = ["cpp"], toolchains = use_cc_toolchain())
