"""Extract a fixed SWIG template tree using a declared Python executable."""

def _swig_lib_extract_impl(ctx):
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add_all(ctx.files.data, before_each = "--archive")
    args.add("--prefix", ctx.attr.strip_prefix)
    args.add("--output", output.path)
    if ctx.attr.require_entry_point:
        args.add("--require-entry-point")
    tool = ctx.attr._extractor[DefaultInfo].files_to_run
    ctx.actions.run(
        executable = tool,
        tools = [tool],
        arguments = [args],
        inputs = ctx.files.data,
        outputs = [output],
        mnemonic = "SwigLibExtract",
        progress_message = "Extracting required SWIG templates for %{label}",
    )
    return [DefaultInfo(files = depset([output]))]

swig_lib_deb = rule(
    implementation = _swig_lib_extract_impl,
    attrs = {
        "data": attr.label(mandatory = True, allow_files = True),
        "strip_prefix": attr.string(default = "usr/share/swig4.0"),
        "require_entry_point": attr.bool(
            default = False,
            doc = "Require swig.swg for a complete template tree; leave false to extract a subtree.",
        ),
        "_extractor": attr.label(default = Label("//swig:extract_templates"), executable = True, cfg = "exec"),
    },
)
