"""Executable build tools with declared interpreters and Debian runtimes."""

PythonRuntimeInfo = provider(fields = ["interpreter", "files"])

def _python_runtime_impl(ctx):
    runtime = ctx.toolchains["@rules_python//python:toolchain_type"].py3_runtime
    if runtime == None or runtime.interpreter == None:
        fail("Build tools require a declared Python 3 interpreter")
    return [
        PythonRuntimeInfo(interpreter = runtime.interpreter, files = runtime.files),
        DefaultInfo(
            files = depset([runtime.interpreter]),
            runfiles = ctx.runfiles(files = [runtime.interpreter], transitive_files = runtime.files),
        ),
    ]

python_runtime = rule(
    implementation = _python_runtime_impl,
    toolchains = ["@rules_python//python:toolchain_type"],
)

def _runtime_impl(ctx):
    python = ctx.attr._python[PythonRuntimeInfo]
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add(ctx.file._prepare)
    args.add("--out", output.path)
    args.add("--architecture", ctx.attr.architecture)
    args.add("--profile", ctx.attr.profile)
    args.add("--launcher", ctx.executable._runtime_launcher.path)
    args.add_all(ctx.files.packages, before_each = "--tar")
    support = ctx.files._test_support if ctx.attr.profile == "tests" else []
    environment = {"LANG": "C", "LC_ALL": "C", "PYTHONHASHSEED": "0"}
    if support:
        environment["PYTHONPATH"] = ":".join(depset([file.dirname for file in support]).to_list())
    ctx.actions.run(
        executable = python.interpreter,
        arguments = [args],
        inputs = ctx.files.packages,
        tools = depset([python.interpreter, ctx.file._prepare, ctx.executable._runtime_launcher] + support, transitive = [python.files]),
        outputs = [output],
        env = environment,
        mnemonic = "PrepareBuildTools",
        progress_message = "Preparing pinned build-tools runtime",
    )
    return [DefaultInfo(files = depset([output]))]

build_tools_runtime = rule(
    implementation = _runtime_impl,
    attrs = {
        "architecture": attr.string(mandatory = True, values = ["amd64", "arm64"]),
        "packages": attr.label_list(allow_files = True, mandatory = True),
        "profile": attr.string(default = "documentation", values = ["documentation", "configure", "kerberos", "foreign", "tests", "perl"]),
        "_prepare": attr.label(default = Label(":prepare_runtime.py"), allow_single_file = True, cfg = "exec"),
        "_test_support": attr.label_list(default = [Label(":test_runtime.py"), Label("//deb:elf_metadata.py")], allow_files = True, cfg = "exec"),
        "_python": attr.label(default = Label(":python_runtime"), providers = [PythonRuntimeInfo], cfg = "exec"),
        "_runtime_launcher": attr.label(default = Label(":runtime_launcher"), executable = True, cfg = "exec"),
    },
)

def _rootfs_impl(ctx):
    python = ctx.attr._python[PythonRuntimeInfo]
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add(ctx.file._prepare)
    args.add("--out", output.path)
    args.add("--architecture", ctx.attr.architecture)
    args.add_all(ctx.files.packages, before_each = "--tar")
    args.add_all(ctx.attr.cross_architectures, before_each = "--cross-architecture")
    if len(ctx.attr.target_data) != len(ctx.attr.target_controls):
        fail("target_data and target_controls must contain matching package pairs")
    for data, control in zip(ctx.attr.target_data, ctx.attr.target_controls):
        data_files = data[DefaultInfo].files.to_list()
        control_files = control[DefaultInfo].files.to_list()
        if len(data_files) != 1 or len(control_files) != 1:
            fail("Each target package requires exactly one data and control tar")
        args.add("--target-package")
        args.add(data_files[0].path)
        args.add(control_files[0].path)
    if len(ctx.attr.metadata_data) != len(ctx.attr.metadata_controls):
        fail("metadata_data and metadata_controls must contain matching package pairs")
    for data, control in zip(ctx.attr.metadata_data, ctx.attr.metadata_controls):
        data_files = data[DefaultInfo].files.to_list()
        control_files = control[DefaultInfo].files.to_list()
        if len(data_files) != 1 or len(control_files) != 1:
            fail("Each package metadata entry requires exactly one data and control tar")
        args.add("--metadata")
        args.add(data_files[0].path)
        args.add(control_files[0].path)
    ctx.actions.run(
        executable = python.interpreter,
        arguments = [args],
        inputs = ctx.files.packages + ctx.files.metadata_data + ctx.files.metadata_controls + ctx.files.target_data + ctx.files.target_controls,
        tools = depset([python.interpreter, ctx.file._prepare, ctx.file._extract, ctx.file._relocate, ctx.file._debian], transitive = [python.files]),
        outputs = [output],
        env = {"LANG": "C", "LC_ALL": "C", "PYTHONHASHSEED": "0"},
        mnemonic = "PrepareBuildToolsRootfs",
        progress_message = "Preparing declared Debian kernel build tools",
    )
    return [DefaultInfo(files = depset([output]))]

build_tools_rootfs = rule(
    implementation = _rootfs_impl,
    attrs = {
        "architecture": attr.string(mandatory = True, values = ["amd64", "arm64"]),
        "packages": attr.label_list(allow_files = True, mandatory = True),
        "cross_architectures": attr.string_list(),
        "target_data": attr.label_list(allow_files = True),
        "target_controls": attr.label_list(allow_files = True),
        "metadata_data": attr.label_list(allow_files = True),
        "metadata_controls": attr.label_list(allow_files = True),
        "_prepare": attr.label(default = Label(":prepare_rootfs.py"), allow_single_file = True, cfg = "exec"),
        "_extract": attr.label(default = Label(":prepare_runtime.py"), allow_single_file = True, cfg = "exec"),
        "_relocate": attr.label(default = Label(":prepare_kernel.py"), allow_single_file = True, cfg = "exec"),
        "_debian": attr.label(default = Label(":kernel_debian.py"), allow_single_file = True, cfg = "exec"),
        "_python": attr.label(default = Label(":python_runtime"), providers = [PythonRuntimeInfo], cfg = "exec"),
    },
)

def _runfile(ctx, file):
    if file.short_path.startswith("../"):
        return file.short_path[3:]
    return ctx.workspace_name + "/" + file.short_path

def _executable_impl(ctx):
    python = ctx.attr._python[PythonRuntimeInfo]
    executable = ctx.actions.declare_file(ctx.label.name)

    # /bin/sh only bootstraps the declared Python interpreter. Every operation
    # here is a shell builtin, so invocation also works with an empty PATH.
    # An executable used as test data lives inside its parent's runfiles tree;
    # an executable passed through FilesToRunProvider has its own sibling tree.
    script = """#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
runfiles="$self.runfiles"
if [ ! -d "$runfiles" ] && [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR" ]; then
    runfiles=$RUNFILES_DIR
fi
if [ ! -d "$runfiles" ]; then
    runfiles=$self
    while :; do
        case "$runfiles" in
            *.runfiles) break ;;
            /|"") echo "Missing build-tool runfiles for $self" >&2; exit 1 ;;
        esac
        runfiles=${runfiles%/*}
    done
fi
exec "$runfiles/{python}" "$runfiles/{launcher}" "{tool}" "$runfiles/{runtime}" "$@"
""".replace("{python}", _runfile(ctx, python.interpreter)).replace("{launcher}", _runfile(ctx, ctx.file._launcher)).replace("{tool}", ctx.attr.tool).replace("{runtime}", _runfile(ctx, ctx.file.runtime))
    ctx.actions.write(executable, script, is_executable = True)
    return [DefaultInfo(
        executable = executable,
        runfiles = ctx.runfiles(
            files = [python.interpreter, ctx.file._launcher, ctx.file.runtime],
            transitive_files = python.files,
        ),
    )]

build_tool = rule(
    implementation = _executable_impl,
    executable = True,
    attrs = {
        "tool": attr.string(mandatory = True, values = ["aspell", "doxygen", "make", "m4", "pkgconf", "perl", "bison", "flex", "autoconf", "autoheader", "autom4te", "compile_et", "mk_cmds", "autoreconf", "automake", "aclocal", "libtoolize", "protoc", "grpc_cpp_plugin", "grpc_python_plugin", "thrift", "python3", "sphinx-build", "makeinfo", "dot", "valgrind", "tclsh", "po2debconf", "msgcat"]),
        "_launcher": attr.label(default = Label(":launcher.py"), allow_single_file = True, cfg = "exec"),
        "runtime": attr.label(default = Label(":runtime"), allow_single_file = True, cfg = "exec"),
        "_python": attr.label(default = Label(":python_runtime"), providers = [PythonRuntimeInfo], cfg = "exec"),
    },
)

def _check_impl(ctx):
    python = ctx.attr._python[PythonRuntimeInfo]
    output = ctx.actions.declare_file(ctx.label.name + ".json")
    args = ctx.actions.args()
    args.add(ctx.file._test)
    args.add(ctx.executable.aspell)
    args.add(ctx.executable.doxygen)
    args.add("--report", output)
    ctx.actions.run(
        executable = python.interpreter,
        arguments = [args],
        tools = [
            ctx.attr.aspell[DefaultInfo].files_to_run,
            ctx.attr.doxygen[DefaultInfo].files_to_run,
            ctx.file._test,
        ] + python.files.to_list(),
        outputs = [output],
        env = {"LANG": "C", "LC_ALL": "C", "PYTHONHASHSEED": "0"},
        mnemonic = "CheckBuildTools",
    )
    return [DefaultInfo(files = depset([output]))]

check_build_tools = rule(
    implementation = _check_impl,
    attrs = {
        "aspell": attr.label(executable = True, cfg = "exec", mandatory = True),
        "doxygen": attr.label(executable = True, cfg = "exec", mandatory = True),
        "_test": attr.label(default = Label(":runtime_test.py"), allow_single_file = True, cfg = "exec"),
        "_python": attr.label(default = Label(":python_runtime"), providers = [PythonRuntimeInfo], cfg = "exec"),
    },
)
