"""Import existing Debian packages as ordered image payloads and metadata."""

DebImportInfo = provider(fields = ["payload", "manifest", "controls"])

def _deb_import_impl(ctx):
    packages = []
    for target in ctx.attr.srcs:
        files = target[DefaultInfo].files.to_list()
        if len(files) != 1 or not files[0].basename.endswith(".deb"):
            fail("deb_import requires one existing .deb per src: %s" % target.label)
        packages.append(files[0])
    if not packages:
        fail("deb_import requires at least one existing .deb")
    payload = ctx.actions.declare_file(ctx.label.name + "/payload.tar")
    manifest = ctx.actions.declare_file(ctx.label.name + "/manifest.json")
    controls = [ctx.actions.declare_file(ctx.label.name + "/controls/%s.tar" % i) for i in range(len(packages))]
    mapping = ctx.actions.declare_file(ctx.label.name + ".inputs.json")
    ctx.actions.write(mapping, json.encode({
        "architecture": ctx.attr.architecture,
        "metadata": json.decode(ctx.attr.metadata),
        "packages": [{"path": package.path, "label": str(target.label), "control": control.path} for package, target, control in zip(packages, ctx.attr.srcs, controls)],
    }))
    args = ctx.actions.args()
    args.add("--inputs", mapping)
    args.add("--payload", payload)
    args.add("--manifest", manifest)
    inputs = packages + [mapping]
    if ctx.file.runtime_manifest:
        args.add("--runtime-manifest", ctx.file.runtime_manifest)
        inputs.append(ctx.file.runtime_manifest)
    ctx.actions.run(
        executable = ctx.attr._tool[DefaultInfo].files_to_run,
        arguments = [args],
        inputs = inputs,
        outputs = [payload, manifest] + controls,
        mnemonic = "ImportDebPayloads",
        progress_message = "Importing existing Debian packages for %{label}",
    )
    return [
        DefaultInfo(files = depset([payload])),
        DebImportInfo(payload = payload, manifest = manifest, controls = depset(controls)),
        OutputGroupInfo(manifest = depset([manifest]), controls = depset(controls)),
    ]

_deb_import = rule(
    implementation = _deb_import_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = [".deb"], mandatory = True),
        "architecture": attr.string(mandatory = True),
        "metadata": attr.string(default = "{}"),
        "runtime_manifest": attr.label(allow_single_file = [".json"]),
        "_tool": attr.label(default = Label("//deb:import_debs"), executable = True, cfg = "exec"),
    },
)

def deb_import(name, srcs, architecture, metadata = {}, runtime_manifest = None, **kwargs):
    """Read declared DEBs without building packages or running maintainer scripts.

    Args:
        name: Target name; the default output is name/payload.tar.
        srcs: Existing .deb labels in installation order. Later entries overlay earlier ones.
        architecture: Accepted Debian architecture (Architecture: all is also accepted).
        metadata: Consumer context recorded alongside verified package facts. Cannot
            replace schema, architecture, packages, payload or runtime manifest digest.
        runtime_manifest: Optional parent import manifest. Rejects different bytes for
            a package already present in that parent, and records its exact digest.
        **kwargs: Common Bazel rule attributes.

    The manifest output group supplies name/manifest.json; controls supplies the
    unmodified decompressed control archives. Import preserves ownership, modes,
    links and ordering. It is extraction, not dpkg installation.
    """
    _deb_import(name = name, srcs = srcs, architecture = architecture, metadata = json.encode(metadata), runtime_manifest = runtime_manifest, **kwargs)
