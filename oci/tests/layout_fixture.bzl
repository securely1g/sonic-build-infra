"""Make a tiny OCI layout from the native runtime TAR for rule integration."""

def _layout_impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name)
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = [ctx.file.tar.path, out.path, ctx.attr.architecture],
        inputs = [ctx.file.tar],
        outputs = [out],
    )
    return [DefaultInfo(files = depset([out]))]

layout_fixture = rule(
    implementation = _layout_impl,
    attrs = {
        "tar": attr.label(allow_single_file = True, mandatory = True),
        "architecture": attr.string(mandatory = True),
        "_tool": attr.label(default = "//oci:layout_fixture", executable = True, cfg = "exec"),
    },
)
