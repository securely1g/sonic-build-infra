"""Assemble and filter source or Debian package archives in one action."""

load("//binary:debug_symbols.bzl", "DebugSymbolsInfo")

def _assemble_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".tar")
    args = ctx.actions.args()
    args.add("--output", output)
    args.add_all(ctx.files.excludes, before_each = "--exclude")
    args.add_all(ctx.attr.includes, before_each = "--include")
    if ctx.attr.root_owned:
        args.add("--root-owned")
    args.add_all(ctx.files.archives, expand_directories = False)
    ctx.actions.run(
        executable = ctx.attr._tool[DefaultInfo].files_to_run,
        arguments = [args],
        inputs = ctx.files.archives + ctx.files.excludes,
        outputs = [output],
        mnemonic = "AssembleOciLayer",
        progress_message = "Assembling OCI layer %{label}",
    )
    debug_deps = ctx.attr.archives + ctx.attr.debug_symbol_deps
    return [
        DefaultInfo(files = depset([output])),
        DebugSymbolsInfo(symbols = depset(transitive = [
            dep[DebugSymbolsInfo].symbols
            for dep in debug_deps
            if DebugSymbolsInfo in dep
        ])),
    ]

assemble_layer = rule(
    implementation = _assemble_impl,
    attrs = {
        "archives": attr.label_list(allow_files = True, mandatory = True),
        "debug_symbol_deps": attr.label_list(allow_files = True),
        "excludes": attr.label_list(allow_files = True),
        "includes": attr.string_list(),
        "root_owned": attr.bool(),
        "_tool": attr.label(default = Label("//oci:layer_assembly"), executable = True, cfg = "exec"),
    },
)
