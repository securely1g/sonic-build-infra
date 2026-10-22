"""Add service accounts while preserving the checked OCI base's accounts."""

load("@rules_distroless//distroless:defs.bzl", "group", "passwd")
load("@tar.bzl", "tar")

def _merge_impl(ctx):
    if not ctx.file.base.is_directory:
        fail("account_layer requires an OCI layout directory")
    outputs = {"passwd": ctx.outputs.passwd, "group": ctx.outputs.group, "json": ctx.outputs.receipt}
    args = ctx.actions.args()
    args.add("--base", ctx.file.base.path)
    args.add("--users", ctx.file.users)
    args.add("--groups", ctx.file.groups)
    args.add("--architecture", ctx.attr.architecture)
    args.add("--passwd-out", outputs["passwd"])
    args.add("--group-out", outputs["group"])
    args.add("--receipt", outputs["json"])
    ctx.actions.run(
        executable = ctx.executable._tool,
        arguments = [args],
        inputs = [ctx.file.base, ctx.file.users, ctx.file.groups],
        outputs = outputs.values(),
        mnemonic = "MergeImageAccounts",
    )
    return [DefaultInfo(files = depset(outputs.values()))]

_merge = rule(
    implementation = _merge_impl,
    attrs = {
        "passwd": attr.output(mandatory = True),
        "group": attr.output(mandatory = True),
        "receipt": attr.output(mandatory = True),
        "base": attr.label(allow_single_file = True, mandatory = True),
        "users": attr.label(allow_single_file = True, mandatory = True),
        "groups": attr.label(allow_single_file = True, mandatory = True),
        "architecture": attr.string(mandatory = True, values = ["amd64", "arm64"]),
        "_tool": attr.label(default = Label("//oci:accounts"), executable = True, cfg = "exec"),
    },
)

def account_layer(name, base, architecture, users = [], groups = [], **kwargs):
    """Emit one account TAR and <name>.receipt; declare only added service accounts."""

    # passwd consumes optional dict keys; copy entries before calling upstream.
    passwd(name = name + "_users", entries = [dict(entry) for entry in users], **kwargs)
    group(name = name + "_groups", entries = groups, **kwargs)
    state = name + "_state"
    _merge(name = state, passwd = state + ".passwd", group = state + ".group", receipt = state + ".json", base = base, architecture = architecture, users = ":" + name + "_users", groups = ":" + name + "_groups", **kwargs)
    tar(
        name = name,
        srcs = [":" + state + ".passwd", ":" + state + ".group"],
        mtree = [
            "./etc uid=0 gid=0 mode=0755 time=0 type=dir",
            "./etc/passwd uid=0 gid=0 mode=0644 time=0 type=file content=$(location :" + state + ".passwd)",
            "./etc/group uid=0 gid=0 mode=0644 time=0 type=file content=$(location :" + state + ".group)",
        ],
        **kwargs
    )
    native.filegroup(name = name + ".receipt", srcs = [":" + state + ".json"], **kwargs)
