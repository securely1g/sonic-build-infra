"""Compress the Docker-save TAR stream without an intermediate archive."""

_TAR_TOOLCHAIN = "@tar.bzl//tar/toolchain:type"

def _compressed_archive_impl(ctx):
    tar = ctx.toolchains[_TAR_TOOLCHAIN]
    runfiles = ctx.attr.loader[DefaultInfo].default_runfiles.files
    spec_path = ctx.executable.loader.dirname + "/" + ctx.attr.loader.label.name + "/tarball.spec"
    specs = [file for file in runfiles.to_list() if file.path == spec_path]
    if len(specs) != 1:
        fail("Expected one Docker archive mtree specification for {}".format(ctx.attr.loader.label))
    archive = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.run_shell(
        inputs = depset(transitive = [runfiles, tar.default.files]),
        tools = [tar.tarinfo.binary, ctx.attr._pigz[DefaultInfo].files_to_run],
        outputs = [archive],
        arguments = [tar.tarinfo.binary.path, specs[0].path, ctx.executable._pigz.path, archive.path],
        command = """
set -euo pipefail
"$1" --create --no-xattr --no-mac-metadata --blocking-factor 1 --file - "@$2" |
    "$3" --no-name -6 > "$4"
""",
        mnemonic = "CompressedDockerArchive",
        progress_message = "Exporting compressed Docker archive %{label}",
    )
    return [DefaultInfo(files = depset([archive]))]

compressed_docker_archive = rule(
    implementation = _compressed_archive_impl,
    attrs = {
        "loader": attr.label(executable = True, cfg = "target", mandatory = True),
        "_pigz": attr.label(
            default = Label("//tools/build_tools:pigz"),
            executable = True,
            cfg = "exec",
        ),
    },
    toolchains = [_TAR_TOOLCHAIN],
)
