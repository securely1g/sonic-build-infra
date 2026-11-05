"""Native semantic test built from pkg-config queries rather than CcInfo flags."""
load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cpp_toolchain", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")

def _impl(ctx):
    tc = find_cpp_toolchain(ctx)
    features = cc_common.configure_features(ctx = ctx, cc_toolchain = tc, requested_features = ctx.features, unsupported_features = ctx.disabled_features)
    variables = cc_common.create_compile_variables(feature_configuration = features, cc_toolchain = tc)
    compiler = [cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.c_compile)] + cc_common.get_memory_inefficient_command_line(feature_configuration = features, action_name = ACTION_NAMES.c_compile, variables = variables)
    variables = cc_common.create_link_variables(feature_configuration = features, cc_toolchain = tc, is_linking_dynamic_library = False)
    link_flags = cc_common.get_memory_inefficient_command_line(feature_configuration = features, action_name = ACTION_NAMES.cpp_link_executable, variables = variables)
    files = []
    headers = []
    for dep in ctx.attr.deps:
        info = dep[CcInfo]
        headers.append(info.compilation_context.headers)
        for item in info.linking_context.linker_inputs.to_list():
            files.extend(item.additional_inputs)
            for library in item.libraries:
                files.extend([f for f in [library.static_library, library.pic_static_library, library.interface_library, library.dynamic_library, library.resolved_symlink_dynamic_library] if f])
    spec = ctx.actions.declare_file(ctx.label.name + ".json")
    ctx.actions.write(spec, json.encode({"compiler": compiler, "link_flags": link_flags, "source": ctx.file.src.path, "build_pc": ctx.file.pc.path, "apt_pc": ctx.file.apt_pc.path, "module": ctx.attr.module, "version": ctx.attr.version, "pkgconf": ctx.file.runtime.path, "native_dynamic": ctx.attr.native_dynamic, "library_dirs": depset([f.dirname for f in files]).to_list()}))
    output = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.run(
        executable = ctx.attr._python[DefaultInfo].files_to_run,
        arguments = [ctx.file._runner.path, spec.path, output.path],
        inputs = depset(files + [ctx.file.src, ctx.file.pc, ctx.file.apt_pc, ctx.file.runtime, ctx.file._runner, spec], transitive = headers + [tc.all_files]),
        outputs = [output],
        tools = [ctx.attr._python[DefaultInfo].files_to_run],
        mnemonic = "PkgconfigNativeConsumer",
    )
    return [DefaultInfo(executable = output)]

pkgconfig_consumer_test = rule(
    implementation = _impl,
    test = True,
    attrs = {
        "src": attr.label(allow_single_file = True),
        "pc": attr.label(allow_single_file = [".pc"]),
        "apt_pc": attr.label(allow_single_file = [".pc"]),
        "deps": attr.label_list(providers = [CcInfo]),
        "native_dynamic": attr.bool(default = False),
        "module": attr.string(),
        "version": attr.string(),
        "runtime": attr.label(allow_single_file = True, cfg = "exec"),
        "_python": attr.label(default = "//tools/build_tools:python3", executable = True, cfg = "exec"),
        "_runner": attr.label(default = ":build_consumer.py", allow_single_file = True),
    },
    toolchains = use_cc_toolchain(),
    fragments = ["cpp"],
)

def _ordinary_impl(ctx):
    return [CcInfo(compilation_context = cc_common.create_compilation_context(includes = depset(["ordinary"]))) ]
ordinary_includes = rule(implementation = _ordinary_impl)

def _unsupported_test_impl(ctx):
    env = analysistest.begin(ctx)
    asserts.expect_failure(env, "ordinary -I includes are not supported")
    return analysistest.end(env)
unsupported_includes_test = analysistest.make(_unsupported_test_impl, expect_failure = True)

# Distinct linker inputs may carry the same dynamic LibraryToLink, as happens
# when a native cc_shared_library exports its transitive dependency graph.
def _repeated_dynamic_impl(ctx):
    info = ctx.attr.dep[CcInfo]
    libraries = [lib for item in info.linking_context.linker_inputs.to_list() for lib in item.libraries]
    inputs = [cc_common.create_linker_input(
        owner = ctx.label,
        libraries = depset(libraries),
        user_link_flags = depset(["-Wl,--defsym,duplicate_marker_" + str(index) + "=1"]),
    ) for index in range(300)]
    return [CcInfo(compilation_context = info.compilation_context,
                   linking_context = cc_common.create_linking_context(linker_inputs = depset(inputs)))]
repeated_dynamic = rule(implementation = _repeated_dynamic_impl, attrs = {"dep": attr.label(providers = [CcInfo])})
