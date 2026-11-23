"""Assemble owner runtime archives and their package receipt."""

def _source_runtime_impl(ctx):
    args = ctx.actions.args()
    args.add("--base", ctx.file.base.path)
    args.add("--contract", ctx.file.contract)
    args.add("--out-tar", ctx.outputs.archive)
    args.add("--receipt", ctx.outputs.receipt)
    args.add("--reuse-inventory")
    inputs = [ctx.file.base, ctx.file.contract]
    if ctx.file.base_inventory:
        inputs.append(ctx.file.base_inventory)
        args.add("--base-inventory", ctx.file.base_inventory)
    for name, target in ctx.attr.modules.items():
        module = target[DefaultInfo].files.to_list()
        if len(module) != 1:
            fail("Expected one MODULE.bazel for " + name)
        inputs.append(module[0])
        args.add("--module", name + "=" + module[0].path)
    for name, target in ctx.attr.packages.items():
        archive = target[DefaultInfo].files.to_list()
        if len(archive) != 1:
            fail("Expected one package TAR for " + name)
        inputs.append(archive[0])
        args.add("--package", name + "=" + archive[0].path)
        args.add("--package-target", name + "=" + ctx.attr.package_targets[name])
    outputs = [ctx.outputs.archive, ctx.outputs.receipt]
    ctx.actions.run(
        executable = ctx.attr.tool[DefaultInfo].files_to_run,
        inputs = inputs,
        outputs = outputs,
        arguments = [args],
        mnemonic = ctx.attr.mnemonic,
    )
    return [DefaultInfo(files = depset(outputs))]

_source_runtime = rule(
    implementation = _source_runtime_impl,
    attrs = {
        "archive": attr.output(mandatory = True),
        "base": attr.label(allow_single_file = True, mandatory = True),
        "base_inventory": attr.label(allow_single_file = [".json"]),
        "contract": attr.label(allow_single_file = [".json"], mandatory = True),
        "mnemonic": attr.string(default = "AssembleSourceRuntime"),
        "modules": attr.string_keyed_label_dict(allow_files = True),
        "packages": attr.string_keyed_label_dict(allow_files = True),
        "package_targets": attr.string_dict(),
        "receipt": attr.output(mandatory = True),
        "tool": attr.label(executable = True, cfg = "exec", mandatory = True),
    },
)

def source_runtime(name, packages, **kwargs):
    _source_runtime(name = name, packages = packages, package_targets = packages, **kwargs)
