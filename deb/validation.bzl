"""Check the final archive against the source producer's declared inputs."""

def _validate_impl(ctx):
    architecture = None
    for cpu, value in [("x86_64", "amd64"), ("aarch64", "arm64"), ("armv7", "armhf")]:
        if ctx.target_platform_has_constraint(getattr(ctx.attr, "_" + cpu)[platform_common.ConstraintValueInfo]):
            architecture = value
    if not architecture:
        fail("Package validation requires amd64, arm64 or armhf")
    output = ctx.actions.declare_file(ctx.label.name + ".json")
    tar = ctx.toolchains["@tar.bzl//tar/toolchain:type"]
    args = ctx.actions.args()
    inputs = [ctx.file.deb, ctx.file.data, ctx.file.control]
    for name in ["deb", "data", "control"]:
        args.add("--" + name, getattr(ctx.file, name))
    for name in ["package", "version"]:
        args.add("--" + name, getattr(ctx.attr, name))
    args.add("--architecture", architecture)
    args.add("--tar", tar.tarinfo.binary)
    args.add("--output", output)
    for target, name in ctx.attr.control_files.items():
        files = target[DefaultInfo].files.to_list()
        if len(files) != 1:
            fail("Each control-file target must provide one file")
        inputs.extend(files)
        args.add("--control-file", name + "=" + files[0].path)
    ctx.actions.run(
        executable = ctx.executable._validator,
        arguments = [args],
        inputs = inputs,
        outputs = [output],
        tools = tar.default.files,
        mnemonic = "DebianPackageValidation",
        progress_message = "Checking produced Debian package " + ctx.attr.package,
    )
    return [DefaultInfo(files = depset([output]))]

validate_debian_package = rule(
    implementation = _validate_impl,
    attrs = {
        "deb": attr.label(allow_single_file = True, mandatory = True),
        "data": attr.label(allow_single_file = True, mandatory = True),
        "control": attr.label(allow_single_file = True, mandatory = True),
        "control_files": attr.label_keyed_string_dict(allow_files = True),
        "package": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
        "_validator": attr.label(default = Label(":validate_package"), executable = True, cfg = "exec"),
        "_x86_64": attr.label(default = Label("@platforms//cpu:x86_64")),
        "_aarch64": attr.label(default = Label("@platforms//cpu:aarch64")),
        "_armv7": attr.label(default = Label("@platforms//cpu:armv7")),
    },
    toolchains = ["@tar.bzl//tar/toolchain:type"],
)
