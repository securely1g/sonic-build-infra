"""Install declared wheel artifacts into a reproducible OCI filesystem layer."""

def _wheel_layer_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".tar")
    args = ctx.actions.args()
    args.add("--output", output)
    args.add("--site-packages", ctx.attr.site_packages)
    args.add("--scripts", ctx.attr.scripts)
    args.add("--headers", ctx.attr.headers)
    args.add("--data", ctx.attr.data_prefix)
    args.add("--interpreter", ctx.attr.interpreter)
    args.add_all(ctx.files.wheels)
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = [args],
        inputs = ctx.files.wheels,
        outputs = [output],
        mnemonic = "WheelLayer",
        progress_message = "Install Python wheels for %{label}",
    )
    return [DefaultInfo(files = depset([output]))]

wheel_layer = rule(
    implementation = _wheel_layer_impl,
    doc = """Install wheels, including metadata, data files and entry points.

    Inputs must contain the full runtime dependency closure. This rule does not
    resolve or download dependencies, execute package code, or compile bytecode.
    The caller supplies paths for the target image, independently of the Python
    interpreter running the installation tool on the execution platform.
    """,
    attrs = {
        "wheels": attr.label_list(mandatory = True, allow_files = [".whl"]),
        "site_packages": attr.string(mandatory = True),
        "scripts": attr.string(default = "/usr/local/bin"),
        "headers": attr.string(default = "/usr/local/include"),
        "data_prefix": attr.string(default = "/usr/local"),
        "interpreter": attr.string(default = "/usr/bin/python3"),
        "_tool": attr.label(
            default = Label("//python:wheel_layer_builder"),
            executable = True,
            cfg = "exec",
        ),
    },
)
