"""Native target ELF execution with a declared matching distribution runtime."""

load("@rules_python//python:py_info.bzl", "PyInfo")

def _runfile(ctx, file):
    return file.short_path[3:] if file.short_path.startswith("../") else ctx.workspace_name + "/" + file.short_path

def _quote(value):
    return "'" + value.replace("'", "'\\''") + "'"

def _impl(ctx):
    executable = ctx.actions.declare_file(ctx.label.name)
    python = ctx.attr._python[platform_common.ToolchainInfo].py3_runtime
    if python == None or python.interpreter == None:
        fail("Native runtime execution requires a declared Python interpreter")
    sources = ctx.attr._metadata[PyInfo].transitive_sources.to_list()
    metadata = [source for source in sources if source.basename == "elf_metadata.py"]
    if len(metadata) != 1:
        fail("Expected the declared ELF metadata parser source")
    metadata_directory = _runfile(ctx, metadata[0]).rsplit("/", 1)[0]
    # -E isolates only helper initialization. The target keeps the caller's
    # environment, including embedded-Python HOME/PATH values used by clippy.
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]=sys.argv.pop(1);runpy.run_path(sys.argv[0],run_name='__main__')"
    command = 'exec "$runfiles/%s" -E -c %s "$runfiles/%s" "$runfiles/%s" --binary "$runfiles/%s"' % (
        _runfile(ctx, python.interpreter), _quote(bootstrap), metadata_directory,
        _runfile(ctx, ctx.file._runner_source), _runfile(ctx, ctx.file.binary),
    )
    for payload in ctx.files.runtime_payloads:
        command += ' --payload "$runfiles/%s"' % _runfile(ctx, payload)
    if ctx.executable.instrument:
        command += ' --instrument "$runfiles/%s"' % _runfile(ctx, ctx.executable.instrument)
        for argument in ctx.attr.instrument_args:
            command += " " + _quote("--instrument-arg=" + argument)
    command += ' -- "$@"\n'
    ctx.actions.write(executable, """#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
runfiles="$self.runfiles"
if [ ! -d "$runfiles" ] && [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR" ]; then
    runfiles=$RUNFILES_DIR
fi
if [ ! -d "$runfiles" ] && [ -n "${TEST_SRCDIR:-}" ] && [ -d "$TEST_SRCDIR" ]; then
    runfiles=$TEST_SRCDIR
fi
if [ ! -d "$runfiles" ]; then
    runfiles=$self
    while :; do
        case "$runfiles" in
            *.runfiles) break ;;
            /|"") echo "Missing native target runtime runfiles: $self" >&2; exit 1 ;;
        esac
        runfiles=${runfiles%/*}
    done
fi
export RUNFILES_DIR="$runfiles"
""" + command, is_executable = True)
    runfiles = ctx.runfiles(files = [ctx.file.binary, ctx.file._runner_source, python.interpreter] + sources + ctx.files.runtime_payloads + ctx.files.data, transitive_files = python.files)
    targets = [ctx.attr.binary, ctx.attr._metadata] + ctx.attr.data
    if ctx.attr.instrument:
        runfiles = runfiles.merge(ctx.runfiles(files = [ctx.executable.instrument]))
        targets.append(ctx.attr.instrument)
    for target in targets:
        runfiles = runfiles.merge(target[DefaultInfo].default_runfiles).merge(target[DefaultInfo].data_runfiles)
    return [DefaultInfo(executable = executable, runfiles = runfiles)]

_attrs = {
    "binary": attr.label(allow_single_file = True, mandatory = True),
    "runtime_payloads": attr.label_list(allow_files = True, mandatory = True),
    "data": attr.label_list(allow_files = True),
    "instrument": attr.label(executable = True, cfg = "exec", doc = "Declared instrumentation tool invoked before the target loader and ELF."),
    "instrument_args": attr.string_list(),
    "_runner_source": attr.label(default = Label("//cc:runtime_test.py"), allow_single_file = True, cfg = "exec"),
    "_metadata": attr.label(default = Label("//deb:elf_metadata"), providers = [PyInfo], cfg = "exec"),
    "_python": attr.label(default = Label("@rules_python//python:current_py_toolchain"), cfg = "exec"),
}

native_runtime_binary = rule(implementation = _impl, executable = True, attrs = _attrs, doc = "Wrap a native ELF with declared matching libc6 and library payloads; consumers may select cfg=exec.")
native_runtime_test = rule(implementation = _impl, test = True, attrs = _attrs, doc = "Test a native ELF using declared libc6 and target library payloads. Cross targets are compile-only.")
