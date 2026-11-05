"""Runtime-lock plans and checked TARs for OCI and rootfs consumers."""

load("@rules_distroless//distroless:defs.bzl", "flatten")
load(":source_package.bzl", "PythonRuntimeInfo", "python_runtime_packages")

def _run(ctx, arguments, inputs, outputs, mnemonic):
    runtime = ctx.attr._python[platform_common.ToolchainInfo].py3_runtime
    if runtime == None or runtime.interpreter == None:
        fail("runtime locking requires a declared execution Python interpreter")
    args = ctx.actions.args()
    args.add(ctx.executable._tool)
    args.add_all(arguments, expand_directories = False)
    ctx.actions.run(
        executable = runtime.interpreter,
        arguments = [args],
        inputs = depset(inputs, transitive = [runtime.files]),
        tools = [ctx.attr._tool[DefaultInfo].files_to_run],
        outputs = outputs,
        env = {"PATH": "", "PYTHONUTF8": "1", "PYTHONHASHSEED": "0"},
        use_default_shell_env = False,
        mnemonic = mnemonic,
    )

def _plan_impl(ctx):
    manifest = ctx.actions.declare_file(ctx.label.name + ".manifest.json")
    plan = ctx.actions.declare_file(ctx.label.name + ".plan.json")
    metadata = {}
    for target in ctx.attr.packages:
        for package in target[PythonRuntimeInfo].packages.to_list():
            metadata[package.metadata.path] = struct(file = package.metadata, producer = package.producer)
    constraints = []
    for target in ctx.attr.constraints:
        files = target[DefaultInfo].files.to_list()
        if len(files) != 1:
            fail("constraints must each provide one requirements file: " + str(target.label))
        constraints.append({"label": str(target.label), "path": files[0].path})
    values = {key: getattr(ctx.attr, key) for key in [
        "roots",
        "python_version",
        "python_platform",
        "uv_version",
        "index_urls",
        "find_links",
        "no_index",
        "marker_environment",
        "source_distributions",
    ]}
    values["metadata"] = [{"path": value.file.path, "producer": value.producer} for value in metadata.values()]
    values["constraints"] = constraints
    ctx.actions.write(manifest, json.encode(values))
    _run(
        ctx,
        ["plan", "--manifest", manifest, "--output", plan],
        [manifest] + [value.file for value in metadata.values()] + ctx.files.constraints,
        [plan],
        "PythonRuntimePlan",
    )
    return [DefaultInfo(files = depset([plan]))]

_EXECUTION_ATTRS = {
    "_tool": attr.label(default = Label("//python:runtime_lock"), executable = True, cfg = "exec"),
    "_python": attr.label(default = Label("@rules_python//python:current_py_toolchain"), cfg = "exec"),
}

python_runtime_plan = rule(
    implementation = _plan_impl,
    doc = "Export self-contained source metadata and concrete-target resolver inputs; this action never resolves pip dependencies.",
    attrs = dict(_EXECUTION_ATTRS, **{
        "packages": attr.label_list(providers = [PythonRuntimeInfo]),
        "roots": attr.string_list(mandatory = True),
        "source_distributions": attr.string_list(doc = "Names known to be source-built, including required producers that may be absent."),
        "python_version": attr.string(mandatory = True),
        "python_platform": attr.string(mandatory = True),
        "uv_version": attr.string(mandatory = True),
        "index_urls": attr.string_list(default = ["https://pypi.org/simple"]),
        "find_links": attr.string_list(),
        "no_index": attr.bool(default = False),
        "marker_environment": attr.string_dict(),
        "constraints": attr.label_list(allow_files = True),
    }),
)

def _check_impl(ctx):
    validation = ctx.actions.declare_file(ctx.label.name + ".tar")
    source_tars = depset(transitive = [target[PythonRuntimeInfo].tars for target in ctx.attr.sources]).to_list()
    arguments = [
        "check",
        "--plan",
        ctx.file.plan,
        "--requirements",
        ctx.file.requirements,
        "--lock",
        ctx.file.lock,
        "--inputs",
        ctx.file.inputs,
        "--validation-tar",
        validation,
        "--update-command",
        ctx.attr.update_command,
        "--verify-installation",
    ]
    for archive in source_tars:
        arguments.extend(["--source-tar", archive])
    for archive in ctx.files.third_party:
        arguments.extend(["--third-party-tar", archive])
    _run(
        ctx,
        arguments,
        [ctx.file.plan, ctx.file.requirements, ctx.file.lock, ctx.file.inputs] + source_tars + ctx.files.third_party,
        [validation],
        "PythonRuntimeLockCheck",
    )
    return [DefaultInfo(files = depset([validation]))]

python_runtime_lock_check = rule(
    implementation = _check_impl,
    doc = "Validate reviewed lock inputs read-only and produce an empty TAR only on success.",
    attrs = dict(_EXECUTION_ATTRS, **{
        "plan": attr.label(allow_single_file = True, mandatory = True),
        "requirements": attr.label(allow_single_file = True, mandatory = True),
        "lock": attr.label(allow_single_file = True, mandatory = True),
        "inputs": attr.label(allow_single_file = True, mandatory = True),
        "update_command": attr.string(mandatory = True),
        "sources": attr.label_list(providers = [PythonRuntimeInfo]),
        "third_party": attr.label_list(allow_files = True),
    }),
)

def checked_python_runtime(name, plan, requirements, lock, inputs, sources = [], third_party = [], update_command = "", **kwargs):
    """Assemble runtime TARs with a mandatory, read-only lock freshness check.

    Args:
        name: Final runtime TAR target consumed by OCI or rootfs.
        plan: python_runtime_plan target, independent of the lock.
        requirements: Checked-in exported external dependency requests.
        lock: Checked-in requirements_lock.txt consumed by pip.parse.
        inputs: Checked-in input identity/digest record from runtime_lock update.
        sources: Source installation targets or python_runtime_packages collectors.
        third_party: Installation TARs for the complete locked third-party closure.
        update_command: Actual owner command shown when inputs are missing/stale.
        **kwargs: Common attributes forwarded to the check and flatten rules.
    """
    if not update_command:
        fail("checked_python_runtime requires the actual update_command")
    python_runtime_lock_check(
        name = name + "_lock_check",
        plan = plan,
        requirements = requirements,
        lock = lock,
        inputs = inputs,
        update_command = update_command,
        sources = sources,
        third_party = third_party,
        **kwargs
    )
    python_runtime_packages(name = name + "_sources", deps = sources, **kwargs)
    flatten(
        name = name,
        tars = [":" + name + "_sources"] + third_party + [":" + name + "_lock_check"],
        **kwargs
    )
