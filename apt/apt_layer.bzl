"""Keep SONiC's base-image policy while Distroless owns APT inputs and assembly."""

load("@rules_distroless//apt:defs.bzl", "AptPackageSetInfo")
load("@rules_distroless//distroless:defs.bzl", "flatten")

def _apt_selection_impl(ctx):
    if not ctx.file.base.is_directory:
        fail("apt_layer base must be one OCI directory")
    packages = ctx.attr.package_set[AptPackageSetInfo]
    mapping = ctx.actions.declare_file(ctx.attr.name + ".inputs.json")
    ctx.actions.write(mapping, json.encode({
        "architecture": packages.architecture,
        "dependency_set": packages.dependency_set,
        "locked": [
            {
                "key": package.key,
                "package": package.metadata,
                "payload": package.data.path,
                "control": package.control.path,
            }
            for package in packages.packages
        ],
    }))
    manifest = ctx.actions.declare_file(ctx.attr.name + ".manifest")
    receipt = ctx.actions.declare_file(ctx.attr.name + ".selection.json")
    args = ctx.actions.args()
    args.add("--base", ctx.file.base.path)
    args.add("--lock", packages.lock)
    args.add("--retained-manifest", ctx.file.retained_manifest)
    args.add("--mapping", mapping)
    args.add("--variant", ctx.attr.variant)
    args.add("--out-manifest", manifest)
    args.add("--receipt", receipt)
    ctx.actions.run(
        executable = ctx.attr.selector[DefaultInfo].files_to_run,
        arguments = [args],
        inputs = depset([
            ctx.file.base,
            packages.lock,
            ctx.file.retained_manifest,
            mapping,
        ], transitive = [packages.files]),
        outputs = [manifest, receipt],
        mnemonic = "SelectAptPayloads",
        progress_message = "Checking " + ctx.attr.variant + " APT packages against the SONiC base image",
    )
    return [
        DefaultInfo(files = depset([manifest])),
        OutputGroupInfo(
            selection = depset([receipt]),
            payloads = depset([package.data for package in packages.packages]),
        ),
    ]

_apt_selection = rule(
    implementation = _apt_selection_impl,
    attrs = {
        "base": attr.label(allow_single_file = True, mandatory = True),
        "package_set": attr.label(providers = [AptPackageSetInfo], mandatory = True),
        "retained_manifest": attr.label(allow_single_file = [".json"], mandatory = True),
        "variant": attr.string(mandatory = True),
        "selector": attr.label(mandatory = True, executable = True, cfg = "exec"),
    },
)

def _selected_layer_impl(ctx):
    return [
        DefaultInfo(files = ctx.attr.layer[DefaultInfo].files),
        OutputGroupInfo(selection = ctx.attr.selection[OutputGroupInfo].selection),
    ]

_selected_layer = rule(
    implementation = _selected_layer_impl,
    attrs = {"layer": attr.label(), "selection": attr.label()},
)

def apt_layer(name, package_set, base, retained_manifest, selector, variant, **kwargs):
    """Apply the image's policy, then assemble its selection with Distroless.

    package_set is a public @APT_HUB//:package_set target. The selector writes
    an ordered newline manifest and a receipt; it does not assemble tar files.
    The public target exposes the layer and a `selection` receipt output group.
    """
    _apt_selection(
        name = name + "_selection",
        package_set = package_set,
        base = base,
        retained_manifest = retained_manifest,
        selector = selector,
        variant = variant,
        visibility = ["//visibility:private"],
    )
    native.filegroup(
        name = name + "_payloads",
        srcs = [":" + name + "_selection"],
        output_group = "payloads",
        visibility = ["//visibility:private"],
    )
    flatten(
        name = name + "_flatten",
        tars = [":" + name + "_payloads"],
        tar_manifest = ":" + name + "_selection",
        visibility = ["//visibility:private"],
    )
    _selected_layer(
        name = name,
        layer = ":" + name + "_flatten",
        selection = ":" + name + "_selection",
        **kwargs
    )
