"""Embed selected compiler payload runfiles in the protoc launcher."""

def _rlocation_path(ctx, file):
    if file.short_path.startswith("../"):
        return file.short_path[3:]
    return ctx.workspace_name + "/" + file.short_path

def _protoc_main_impl(ctx):
    output = ctx.outputs.out
    ctx.actions.expand_template(
        template = ctx.file._template,
        output = output,
        substitutions = {
            "@ARCHITECTURE@": repr(ctx.attr.architecture),
            "@PAYLOADS@": repr(sorted([_rlocation_path(ctx, file) for file in ctx.files.payloads])),
        },
    )
    return [DefaultInfo(files = depset([output]))]

protoc_main = rule(
    implementation = _protoc_main_impl,
    attrs = {
        "architecture": attr.string(mandatory = True, values = ["amd64", "arm64"]),
        "out": attr.output(mandatory = True),
        "payloads": attr.label_list(allow_files = True, mandatory = True),
        "_template": attr.label(default = Label("//proto:protoc.py.tpl"), allow_single_file = True),
    },
)
