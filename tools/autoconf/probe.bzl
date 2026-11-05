"""Out-of-tree Autoconf inspection using platform-selected C/C++ inputs."""

load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cpp_toolchain", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

def _impl(ctx):
    tc = find_cpp_toolchain(ctx)
    features = cc_common.configure_features(ctx = ctx, cc_toolchain = tc, requested_features = ctx.features, unsupported_features = ctx.disabled_features)
    contexts = [dep[CcInfo].compilation_context for dep in ctx.attr.deps]
    headers = depset(transitive = [c.headers for c in contexts])
    common = {
        "feature_configuration": features,
        "cc_toolchain": tc,
        "include_directories": depset(transitive = [c.includes for c in contexts]),
        "quote_include_directories": depset(transitive = [c.quote_includes for c in contexts]),
        "system_include_directories": depset(transitive = [c.system_includes for c in contexts] + [c.external_includes for c in contexts]),
        "preprocessor_defines": depset(transitive = [c.defines for c in contexts]),
    }
    compilers = {}
    for key, action, flags in [("CC", ACTION_NAMES.c_compile, ctx.fragments.cpp.conlyopts), ("CXX", ACTION_NAMES.cpp_compile, ctx.fragments.cpp.cxxopts)]:
        variables = cc_common.create_compile_variables(user_compile_flags = ctx.fragments.cpp.copts + flags + ctx.attr.copts, **common)
        executable = cc_common.get_tool_for_action(feature_configuration = features, action_name = action)
        compilers[key] = [executable] + cc_common.get_memory_inefficient_command_line(feature_configuration = features, action_name = action, variables = variables)

    # Toolchains can express the sysroot in their feature-generated argv while
    # leaving CcToolchainInfo.sysroot empty. Use the effective compiler value.
    sysroot = tc.sysroot or ""
    for argument in compilers["CC"]:
        if argument.startswith("--sysroot="):
            sysroot = argument.removeprefix("--sysroot=")
    linker_inputs = depset(transitive = [dep[CcInfo].linking_context.linker_inputs for dep in ctx.attr.deps]).to_list()
    libraries = []
    additional_inputs = []
    library_directories = []
    runtime_directories = []
    operands = []
    seen_dynamic = {}
    seen_search_flags = {}
    for item in linker_inputs:
        additional_inputs.extend(item.additional_inputs)
        for flag in item.user_link_flags:
            search_flag = (flag.startswith("-L") and flag != "-L") or any([flag.startswith(prefix) for prefix in ["-Wl,-rpath=", "-Wl,-rpath,", "-Wl,-rpath-link=", "-Wl,-rpath-link,"]])
            if search_flag:
                if flag not in seen_search_flags:
                    operands.append(flag)
                    seen_search_flags[flag] = True
            else:
                # Unknown/stateful linker options delimit an ordered segment.
                operands.append(flag)
                seen_dynamic = {}
        for library in item.libraries:
            file = library.static_library or library.pic_static_library or library.interface_library or library.dynamic_library
            if file:
                libraries.append(file)
                library_directories.append(file.dirname)
                if library.dynamic_library:
                    libraries.append(library.dynamic_library)
                if library.resolved_symlink_dynamic_library:
                    library_directories.append(library.resolved_symlink_dynamic_library.dirname)
                    libraries.append(library.resolved_symlink_dynamic_library)
                    runtime_directories.append(library.resolved_symlink_dynamic_library.dirname)
                elif library.dynamic_library:
                    runtime_directories.append(library.dynamic_library.dirname)
                if library.alwayslink:
                    operands.extend(["-Wl,--whole-archive", file.path, "-Wl,--no-whole-archive"])
                    seen_dynamic = {}
                elif library.static_library or library.pic_static_library:
                    # Static archives can intentionally repeat after new references.
                    operands.append(file.path)
                    seen_dynamic = {}
                elif file.path not in seen_dynamic:
                    operands.append(file.path)
                    seen_dynamic[file.path] = True
    link_variables = cc_common.create_link_variables(
        feature_configuration = features,
        cc_toolchain = tc,
        user_link_flags = ctx.fragments.cpp.linkopts + ctx.attr.linkopts,
        library_search_directories = depset(library_directories),
        runtime_library_search_directories = depset(runtime_directories),
        is_linking_dynamic_library = False,
    )
    link_flags = cc_common.get_memory_inefficient_command_line(feature_configuration = features, action_name = ACTION_NAMES.cpp_link_executable, variables = link_variables)
    extra_tools = []
    extra_tool_files = []
    for target in ctx.attr.extra_tools:
        info = target[DefaultInfo]
        tool = info.files_to_run
        if tool.executable == None:
            fail("extra_tools must provide executable tools: " + str(target.label))
        extra_tools.append(tool)
        extra_tool_files.extend([info.files, info.default_runfiles.files, info.data_runfiles.files])
    inputs = depset(ctx.files.srcs + [ctx.file.configure] + ctx.files.pkg_config_files + libraries + additional_inputs + ctx.files.pkg_config_sysroot + [ctx.file.file_runtime], transitive = [headers, tc.all_files] + extra_tool_files)
    output = ctx.actions.declare_directory(ctx.label.name + ".probe")
    configuration = {
        "build_triplet": ctx.attr.build_triplet,
        "host_triplet": ctx.attr.host_triplet,
        "configure_args": ctx.attr.configure_args,
        "copts": ctx.attr.copts,
        "linkopts": ctx.attr.linkopts,
        "cache_answers": ctx.attr.cache_answers,
    }
    spec = ctx.actions.declare_file(ctx.label.name + ".probe-input.json")
    ctx.actions.write(spec, json.encode({
        "configure": ctx.file.configure.path,
        "runtime": ctx.file.execution_runtime.path,
        "file_runtime": ctx.file.file_runtime.path,
        "extra_tools": [tool.executable.path for tool in extra_tools],
        "configuration": configuration,
        "compilers": compilers,
        "link_flags": link_flags,
        "runtime_library_directories": depset(runtime_directories).to_list(),
        "libraries": operands,
        "ar": cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.cpp_link_static_library),
        "binutils": {
            "NM": tc.nm_executable,
            "OBJCOPY": cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.objcopy_embed_data),
            "STRIP": cc_common.get_tool_for_action(feature_configuration = features, action_name = ACTION_NAMES.strip),
            "OBJDUMP": tc.objdump_executable,
            "LD": tc.ld_executable,
        },
        "toolchain_inputs": [f.path for f in tc.all_files.to_list()],
        "target": {"cpu": tc.cpu, "sysroot": sysroot},
        "pkg_config_files": [f.path for f in ctx.files.pkg_config_files],
        "pkg_config_sysroot": ctx.file.pkg_config_sysroot.path if ctx.file.pkg_config_sysroot else (sysroot if ctx.attr.pkg_config_use_toolchain_sysroot else ""),
        "pkg_config_fdo_sysroot_rules": ctx.attr.pkg_config_fdo_sysroot_rules,
        "inputs": [f.path for f in inputs.to_list()] + [ctx.file.execution_runtime.path],
        "expected_outputs": ctx.attr.expected_outputs,
    }))
    runner = ctx.attr._runner[DefaultInfo].files_to_run
    ctx.actions.run(executable = runner, arguments = [spec.path, output.path], inputs = depset([spec, ctx.file.execution_runtime], transitive = [inputs]), tools = [runner] + extra_tools, outputs = [output], env = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}, mnemonic = "AutoconfProbe")
    return [DefaultInfo(files = depset([output]))]

autoconf_probe = rule(
    implementation = _impl,
    attrs = {
        "configure": attr.label(allow_single_file = True, mandatory = True),
        "srcs": attr.label_list(allow_files = True),
        "deps": attr.label_list(providers = [CcInfo]),
        "pkg_config_files": attr.label_list(allow_files = [".pc"]),
        "pkg_config_sysroot": attr.label(allow_single_file = True),
        "pkg_config_use_toolchain_sysroot": attr.bool(default = True, doc = "Use the target sysroot for non-relocatable pkg-config files when no explicit sysroot is supplied."),
        "pkg_config_fdo_sysroot_rules": attr.bool(default = False),
        "configure_args": attr.string_list(),
        "extra_tools": attr.label_list(cfg = "exec", doc = "Declared configure-time executables and their complete runfiles."),
        "build_triplet": attr.string(mandatory = True),
        "host_triplet": attr.string(mandatory = True),
        "expected_outputs": attr.string_list(),
        "copts": attr.string_list(),
        "linkopts": attr.string_list(),
        "cache_answers": attr.string_dict(),
        "file_runtime": attr.label(default = Label("//tools/build_tools:kernel_runtime"), allow_single_file = True, cfg = "exec"),
        "execution_runtime": attr.label(default = Label("//tools/build_tools:foreign_runtime"), allow_single_file = True, cfg = "exec"),
        "_runner": attr.label(default = Label(":probe_runner"), executable = True, cfg = "exec"),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)
