"""Produce reusable inventory from one configured OCI base."""

def _impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".json")
    args = ctx.actions.args()
    args.add("--base", ctx.file.base.path)
    args.add("--architecture", ctx.attr.architecture)
    args.add("--out", out)
    ctx.actions.run(
        executable = ctx.attr.tool[DefaultInfo].files_to_run,
        inputs = [ctx.file.base],
        outputs = [out],
        arguments = [args],
        mnemonic = "OciBaseInventory",
    )
    return [DefaultInfo(files = depset([out]))]

oci_base_inventory = rule(
    implementation = _impl,
    attrs = {
        "architecture": attr.string(mandatory = True),
        "base": attr.label(allow_single_file = True, mandatory = True),
        "tool": attr.label(executable = True, cfg = "exec", mandatory = True),
    },
)
