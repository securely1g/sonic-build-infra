"""Supply independently declared inputs to the public installed_packages rule."""

def _impl(ctx):
    base = ctx.actions.declare_directory(ctx.label.name + ".base")
    args = ctx.actions.args()
    args.add("--base", base.path)
    args.add("--architecture", ctx.attr.architecture)
    outputs = [base]
    for name in ["apt", "policy", "owner", "module", "source"]:
        output = getattr(ctx.outputs, name)
        args.add("--" + name, output.path)
        outputs.append(output)
    ctx.actions.run(executable = ctx.executable._tool, arguments = [args], outputs = outputs)
    return [DefaultInfo(files = depset([base]))]

metadata_fixture = rule(
    implementation = _impl,
    attrs = {
        "architecture": attr.string(mandatory = True),
        "apt": attr.output(mandatory = True),
        "policy": attr.output(mandatory = True),
        "owner": attr.output(mandatory = True),
        "module": attr.output(mandatory = True),
        "source": attr.output(mandatory = True),
        "_tool": attr.label(default = Label("//oci/tests:metadata_fixture_tool"), executable = True, cfg = "exec"),
    },
)
