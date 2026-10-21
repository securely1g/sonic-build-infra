"""Exercise declared native/cross NM with its runtime and an empty PATH."""

def _binutils_nm_probe_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".txt")
    ctx.actions.run_shell(
        inputs = [ctx.file.binary],
        tools = [ctx.attr.nm[DefaultInfo].files_to_run],
        outputs = [output],
        arguments = [ctx.executable.nm.path, ctx.file.binary.path, output.path],
        command = """
set -eu
"$1" --version > "$3"
symbols=$("$1" --defined-only "$2")
printf '%s\\n' "$symbols" >> "$3"
case "$symbols" in
    *" T sonic_generated_value"*) ;;
    *) printf '%s\\n' 'Declared NM did not find the generated C symbol' >&2; exit 1 ;;
esac
""",
        env = {"PATH": ""},
        mnemonic = "BinutilsNmProbe",
    )
    return [DefaultInfo(files = depset([output]))]

binutils_nm_probe = rule(
    implementation = _binutils_nm_probe_impl,
    attrs = {
        "binary": attr.label(allow_single_file = True, mandatory = True),
        # Resolve the binutils target for the target CPU; its own toolchain
        # selects a binary that runs on the execution CPU for cross builds.
        "nm": attr.label(executable = True, cfg = "target", mandatory = True),
    },
)
