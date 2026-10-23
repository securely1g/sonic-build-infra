"""Produce an OCI base for the account-layer consumer test."""

def _impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name)
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = ["--output", output.path, "--architecture", ctx.attr.architecture],
        outputs = [output],
    )
    return [DefaultInfo(files = depset([output]))]

fixture_base = rule(
    implementation = _impl,
    attrs = {
        "architecture": attr.string(mandatory = True),
        "_tool": attr.label(default = Label("//oci/tests:fixture"), executable = True, cfg = "exec"),
    },
)
