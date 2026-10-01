"""Normalize all tar ownership headers while preserving payload and file modes."""

def _root_owned_tar_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".tar")
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = [ctx.file.src.path, output.path],
        inputs = [ctx.file.src],
        outputs = [output],
        mnemonic = "RootOwnedTar",
        progress_message = "Normalize ownership in %{label}",
    )
    return [DefaultInfo(files = depset([output]))]

root_owned_tar = rule(
    implementation = _root_owned_tar_impl,
    attrs = {
        "src": attr.label(mandatory = True, allow_single_file = True),
        "_tool": attr.label(
            default = Label("//tar:root_owned_tar"),
            executable = True,
            cfg = "exec",
        ),
    },
)
