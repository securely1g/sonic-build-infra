"""Run Cargo from the execution platform's selected Rust SDK."""
_RUST_TOOLCHAIN = "@rules_rust//rust:toolchain_type"
_SDK = provider(fields = ["cargo", "rustc", "rustdoc", "anchor", "files", "libraries"])

def _sdk_impl(ctx):
    toolchain = ctx.toolchains[_RUST_TOOLCHAIN]
    if toolchain.cargo == None:
        fail("The selected Rust toolchain does not provide Cargo")
    return [_SDK(
        cargo = toolchain.cargo,
        rustc = toolchain.rustc,
        rustdoc = toolchain.rust_doc,
        anchor = toolchain.sysroot_anchor,
        files = toolchain.all_files,
        libraries = toolchain.rustc_lib or depset(),
    ), DefaultInfo(files = toolchain.all_files)]

rust_sdk = rule(implementation = _sdk_impl, toolchains = [_RUST_TOOLCHAIN])

def _runfile(ctx, file):
    return file.short_path[3:] if file.short_path.startswith("../") else ctx.workspace_name + "/" + file.short_path

_BOOTSTRAP = """#!/bin/sh
set -eu
self=$0
case "$self" in /*) ;; *) self="$PWD/$self" ;; esac
runfiles=$self.runfiles
if [ ! -d "$runfiles" ]; then
    runfiles=$self
    while :; do
        case "$runfiles" in
            *.runfiles) break ;;
            /|"") echo "Missing declared Rust SDK runfiles" >&2; exit 1 ;;
        esac
        runfiles=${runfiles%/*}
    done
fi
"""

def _cargo_impl(ctx):
    sdk = ctx.attr._sdk[_SDK]
    wrapper = ctx.actions.declare_file(ctx.label.name + ".rustc")
    rustdoc = ctx.actions.declare_file(ctx.label.name + ".rustdoc")
    cargo = ctx.actions.declare_file(ctx.label.name)
    anchor = _runfile(ctx, sdk.anchor).rsplit("/", 1)[0]
    directories = depset([_runfile(ctx, file).rsplit("/", 1)[0] for file in sdk.libraries.to_list() if ".so" in file.basename]).to_list()
    library_path = ":".join(["$runfiles/" + directory for directory in directories])
    environment = 'LD_LIBRARY_PATH="' + library_path + '"\nexport LD_LIBRARY_PATH\n'
    ctx.actions.write(wrapper, _BOOTSTRAP + environment + 'exec "$runfiles/' + _runfile(ctx, sdk.rustc) + '" --sysroot "$runfiles/' + anchor + '" "$@"\n', is_executable = True)
    ctx.actions.write(rustdoc, _BOOTSTRAP + environment + 'exec "$runfiles/' + _runfile(ctx, sdk.rustdoc) + '" --sysroot "$runfiles/' + anchor + '" "$@"\n', is_executable = True)
    ctx.actions.write(cargo, _BOOTSTRAP + environment + 'RUSTC="$runfiles/' + _runfile(ctx, wrapper) + '"\nRUSTDOC="$runfiles/' + _runfile(ctx, rustdoc) + '"\nexport RUSTC RUSTDOC\ncd "${BUILD_WORKING_DIRECTORY:-${BUILD_WORKSPACE_DIRECTORY:-$PWD}}"\nexec "$runfiles/' + _runfile(ctx, sdk.cargo) + '" "$@"\n', is_executable = True)
    return [DefaultInfo(executable = cargo, runfiles = ctx.runfiles(files = [wrapper, rustdoc], transitive_files = sdk.files))]

cargo_runner = rule(
    implementation = _cargo_impl,
    executable = True,
    attrs = {"_sdk": attr.label(default = Label("//rust:sdk"), providers = [_SDK], cfg = "exec")},
)
