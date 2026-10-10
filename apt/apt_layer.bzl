"""Keep base packages automatically; use public Distroless inputs and flatten."""

load("@rules_distroless//distroless:defs.bzl", "flatten")

def _one_file(target):
    files = target[DefaultInfo].files.to_list()
    if len(files) != 1:
        fail("expected one package archive from %s" % target.label)
    return files[0]

def _apt_selection_impl(ctx):
    if not ctx.file.retained_manifest and not ctx.file.policy:
        fail("apt_layer requires retained_manifest or policy")
    if not ctx.file.base.is_directory:
        fail("apt_layer base must be one OCI directory")
    payloads = [_one_file(target) for target in ctx.attr.data]
    controls = [_one_file(target) for target in ctx.attr.controls]
    mapping = ctx.actions.declare_file(ctx.attr.name + ".inputs.json")
    ctx.actions.write(mapping, json.encode({
        "_generated": {
            "notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.",
            "generator": "sonic-build-infra/apt/apt_layer.bzl",
        },
        "architecture": ctx.attr.architecture,
        "dependency_set": ctx.attr.dependency_set,
        "locked": [
            {"key": key, "payload": payload.path, "control": control.path}
            for key, payload, control in zip(ctx.attr.package_keys, payloads, controls)
        ],
    }))
    selected = ctx.actions.declare_directory(ctx.attr.name + ".archives")
    receipt = ctx.actions.declare_file(ctx.attr.name + ".selection.json")
    args = ctx.actions.args()
    args.add("--base", ctx.file.base.path)
    args.add("--lock", ctx.file.lock)
    policy_inputs = []
    if ctx.file.retained_manifest:
        args.add("--retained-manifest", ctx.file.retained_manifest)
        policy_inputs.append(ctx.file.retained_manifest)
    if ctx.file.policy:
        args.add("--policy", ctx.file.policy)
        policy_inputs.append(ctx.file.policy)
    args.add("--mapping", mapping)
    args.add("--variant", ctx.attr.variant)
    args.add("--out-dir", selected.path)
    args.add("--receipt", receipt)
    inherited_metadata = []
    if ctx.file.base_package_metadata:
        args.add("--base-package-metadata", ctx.file.base_package_metadata)
        inherited_metadata = [ctx.file.base_package_metadata]
    ctx.actions.run(
        executable = ctx.attr.selector[DefaultInfo].files_to_run,
        arguments = [args],
        inputs = depset([ctx.file.base, ctx.file.lock, mapping] + policy_inputs + payloads + controls + inherited_metadata),
        outputs = [selected, receipt],
        mnemonic = "SelectAptPayloads",
        progress_message = "Selecting %s APT additions for the SONiC base image" % ctx.attr.variant,
    )
    return [DefaultInfo(files = depset([selected])), OutputGroupInfo(selection = depset([receipt]))]

_apt_selection = rule(
    implementation = _apt_selection_impl,
    attrs = {
        "architecture": attr.string(mandatory = True),
        "base": attr.label(allow_single_file = True, mandatory = True),
        "base_package_metadata": attr.label(allow_single_file = [".json"]),
        "controls": attr.label_list(allow_files = True),
        "data": attr.label_list(allow_files = True),
        "dependency_set": attr.string(mandatory = True),
        "lock": attr.label(allow_single_file = [".json"], mandatory = True),
        "package_keys": attr.string_list(),
        "policy": attr.label(allow_single_file = [".json"]),
        "retained_manifest": attr.label(allow_single_file = [".json"]),
        "selector": attr.label(mandatory = True, executable = True, cfg = "exec"),
        "variant": attr.string(mandatory = True),
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

def apt_layer(name, packages, lock, dependency_set, base, retained_manifest = None, selector = None, variant = None, architecture = "amd64", base_package_metadata = None, policy = None, **kwargs):
    """Select from all reviewed candidates and flatten their unchanged archives.

    packages maps lock keys to public package labels, e.g. @image_apt//tcpdump.
    The apt_inputs repository rule derives these labels from the checked lock.
    Selection remains automatic and happens against the actual base image.
    Pass the parent layer's selection receipt as base_package_metadata when its
    APT additions are inherited without being written to the dpkg database.
    policy is optional selector-owned JSON, declared and passed as --policy.
    Supply retained_manifest, policy, or both; only provided inputs are passed.
    """
    if selector == None:
        fail("apt_layer requires selector")
    if variant == None:
        fail("apt_layer requires variant")
    keys = sorted(packages)
    _apt_selection(
        name = name + "_selection",
        architecture = architecture,
        base = base,
        base_package_metadata = base_package_metadata,
        controls = [packages[key] + ":control" for key in keys],
        data = [packages[key] + ":data" for key in keys],
        dependency_set = dependency_set,
        lock = lock,
        package_keys = keys,
        policy = policy,
        retained_manifest = retained_manifest,
        selector = selector,
        variant = variant,
        visibility = ["//visibility:private"],
    )
    flatten(
        name = name + "_flatten",
        tars = [":" + name + "_selection"],
        visibility = ["//visibility:private"],
    )
    _selected_layer(name = name, layer = ":" + name + "_flatten", selection = ":" + name + "_selection", **kwargs)
