"""Generate an HTML tree with declared Doxygen and Graphviz execution tools."""

def _doxygen_impl(ctx):
    html = ctx.actions.declare_directory(ctx.label.name + ".html")
    template = ctx.actions.declare_file(ctx.label.name + ".template")
    configuration = ctx.actions.declare_file(ctx.label.name + ".Doxyfile")
    ctx.actions.expand_template(
        template = ctx.file.template,
        output = template,
        substitutions = {
            key: ctx.expand_location(value, targets = ctx.attr.srcs)
            for key, value in ctx.attr.substitutions.items()
        },
    )
    doxygen = ctx.attr._doxygen[DefaultInfo].files_to_run
    dot = ctx.attr._dot[DefaultInfo].files_to_run
    ctx.actions.run_shell(
        inputs = depset([template] + ctx.files.srcs),
        tools = [doxygen, dot],
        outputs = [html, configuration],
        arguments = [template.path, configuration.path, html.path, dot.executable.dirname, doxygen.executable.path],
        command = """set -eu
cat "$1" > "$2"
printf '\\nOUTPUT_DIRECTORY = "%s"\\nHTML_OUTPUT = .\\nDOT_PATH = "%s"\\n' "$3" "$4" >> "$2"
"$5" "$2"
""",
        env = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"},
        mnemonic = "Doxygen",
        progress_message = "Generating HTML documentation for %{label}",
    )
    return [DefaultInfo(files = depset([html]))]

doxygen = rule(
    implementation = _doxygen_impl,
    attrs = {
        "template": attr.label(allow_single_file = True, mandatory = True),
        "srcs": attr.label_list(allow_files = True),
        "substitutions": attr.string_dict(),
        "_doxygen": attr.label(default = Label("//tools/build_tools:doxygen"), executable = True, cfg = "exec"),
        "_dot": attr.label(default = Label("//tools/build_tools:dot"), executable = True, cfg = "exec"),
    },
    doc = "Expand the declared Doxyfile and generate one HTML tree using exec tools.",
)
