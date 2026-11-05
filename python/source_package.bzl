"""Direct Python installation and runtime dependency handoff."""

PythonRuntimeInfo = provider(doc = "Python distribution installation payloads and runtime requirements.", fields = {
    "packages": "Transitive depset of structs pairing distribution/version, TAR and metadata.",
    "tars": "Transitive installation TAR files, suitable for OCI tars.",
    "metadata": "Transitive runtime JSON files, for image dependency validation.",
})

_InstallationInfo = provider(doc = "Direct installation outputs for one distribution.", fields = ["distribution", "version", "metadata"])

def _install_impl(ctx):
    tree = ctx.actions.declare_directory(ctx.label.name + ".tree")
    mtree = ctx.actions.declare_file(ctx.label.name + ".mtree")
    metadata = ctx.actions.declare_file(ctx.label.name + ".runtime.json")
    manifest = ctx.actions.declare_file(ctx.label.name + ".inputs.json")
    files = []
    inputs = []
    for target, dest in ctx.attr.files.items():
        outputs = target[DefaultInfo].files.to_list()
        if len(outputs) != 1 or outputs[0].is_directory:
            fail("files keys must produce exactly one regular file: " + str(target.label))
        files.append({"source": outputs[0].path, "path": dest})
        inputs.append(outputs[0])
    values = {key: getattr(ctx.attr, key) for key in [
        "distribution",
        "version",
        "requires",
        "python_requires",
        "extra_requires",
        "entry_points",
        "python_tag",
        "abi",
        "platform",
        "summary",
        "license",
        "author",
        "author_email",
        "maintainer",
        "maintainer_email",
        "keywords",
        "license_files",
        "homepage",
        "classifiers",
        "project_urls",
        "description_content_type",
        "regular_file_mode",
    ]}
    extra_distinfo_files = []
    for target, dest in ctx.attr.extra_distinfo_files.items():
        outputs = target[DefaultInfo].files.to_list()
        if len(outputs) != 1 or outputs[0].is_directory:
            fail("extra_distinfo_files keys must produce exactly one regular file: " + str(target.label))
        extra_distinfo_files.append({"source": outputs[0].path, "path": dest})
        inputs.append(outputs[0])
    values["extra_distinfo_files"] = extra_distinfo_files
    if ctx.file.description_file:
        values["description_file"] = ctx.file.description_file.path
        inputs.append(ctx.file.description_file)
    values.update({
        "files": files,
        "site_packages": ctx.attr.site_packages,
        "scripts": ctx.attr.scripts,
        "headers": ctx.attr.headers,
        "data_prefix": ctx.attr.data_prefix,
        "interpreter": ctx.attr.interpreter,
    })
    ctx.actions.write(manifest, json.encode(values))
    args = ctx.actions.args()
    args.add(ctx.executable._tool)
    args.add_all([manifest, tree, mtree, metadata], expand_directories = False)
    runtime = ctx.attr._python[platform_common.ToolchainInfo].py3_runtime
    if runtime == None or runtime.interpreter == None:
        fail("source installation requires a declared execution Python interpreter")
    ctx.actions.run(
        executable = runtime.interpreter,
        inputs = depset(inputs + [manifest], transitive = [runtime.files]),
        tools = [ctx.attr._tool[DefaultInfo].files_to_run],
        env = {"PATH": "", "PYTHONUTF8": "1"},
        use_default_shell_env = False,
        outputs = [tree, mtree, metadata],
        arguments = [args],
        mnemonic = "PythonSourceInstall",
    )
    return [
        DefaultInfo(files = depset([tree])),
        OutputGroupInfo(mtree = depset([mtree]), metadata = depset([metadata])),
        _InstallationInfo(distribution = ctx.attr.distribution, version = ctx.attr.version, metadata = metadata),
    ]

source_package_install = rule(
    implementation = _install_impl,
    attrs = {
        "files": attr.label_keyed_string_dict(allow_files = True),
        "distribution": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
        "requires": attr.string_list(),
        "python_requires": attr.string(),
        "extra_requires": attr.string_list_dict(),
        "entry_points": attr.string_list_dict(),
        "python_tag": attr.string(default = "py3"),
        "abi": attr.string(default = "none"),
        "platform": attr.string(default = "any"),
        "summary": attr.string(),
        "license": attr.string(),
        "author": attr.string(),
        "author_email": attr.string(),
        "maintainer": attr.string(),
        "maintainer_email": attr.string(),
        "keywords": attr.string(),
        "license_files": attr.string_list(),
        "homepage": attr.string(),
        "classifiers": attr.string_list(),
        "project_urls": attr.string_dict(),
        "description_file": attr.label(allow_single_file = True),
        "description_content_type": attr.string(),
        "regular_file_mode": attr.string(values = ["", "0644", "0755"]),
        "extra_distinfo_files": attr.label_keyed_string_dict(allow_files = True),
        "site_packages": attr.string(mandatory = True),
        "scripts": attr.string(mandatory = True),
        "headers": attr.string(mandatory = True),
        "data_prefix": attr.string(mandatory = True),
        "interpreter": attr.string(mandatory = True),
        "_python": attr.label(default = Label("@rules_python//python:current_py_toolchain"), cfg = "exec"),
        "_tool": attr.label(default = Label("//python:source_package_builder"), executable = True, cfg = "exec"),
    },
)

def _collect(deps, packages = [], tars = [], metadata = []):
    return PythonRuntimeInfo(
        packages = depset(packages, transitive = [dep[PythonRuntimeInfo].packages for dep in deps]),
        tars = depset(tars, transitive = [dep[PythonRuntimeInfo].tars for dep in deps]),
        metadata = depset(metadata, transitive = [dep[PythonRuntimeInfo].metadata for dep in deps]),
    )

def _runtime_impl(ctx):
    installed = ctx.attr.installation[_InstallationInfo]
    archive = ctx.file.tar
    info = _collect(ctx.attr.runtime_deps, [struct(
        distribution = installed.distribution,
        producer = str(ctx.label),
        version = installed.version,
        tar = archive,
        metadata = installed.metadata,
    )], [archive], [installed.metadata])
    return [
        DefaultInfo(files = depset([archive])),
        info,
        OutputGroupInfo(metadata = info.metadata, runtime_tars = info.tars),
    ]

source_package_runtime = rule(
    implementation = _runtime_impl,
    attrs = {
        "tar": attr.label(allow_single_file = True, mandatory = True),
        "installation": attr.label(providers = [_InstallationInfo], mandatory = True),
        "runtime_deps": attr.label_list(providers = [PythonRuntimeInfo]),
    },
)

def _runtime_packages_impl(ctx):
    info = _collect(ctx.attr.deps)
    return [DefaultInfo(files = info.tars), info, OutputGroupInfo(metadata = info.metadata)]

python_runtime_packages = rule(
    implementation = _runtime_packages_impl,
    doc = "Collect source runtime TARs and metadata for image assembly; dependency resolution belongs to the image.",
    attrs = {"deps": attr.label_list(providers = [PythonRuntimeInfo], mandatory = True)},
)
