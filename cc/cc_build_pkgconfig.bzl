"""Export a source library's actual CcInfo as relocatable build-tree pkg-config."""

load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

def _relative(path, directory):
    path_parts = path.split("/")
    directory_parts = directory.split("/")
    common = 0
    for index in range(min(len(path_parts), len(directory_parts))):
        if path_parts[index] != directory_parts[index]:
            break
        common = index + 1
    return "/".join([".."] * (len(directory_parts) - common) + path_parts[common:]) or "."

def _word(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

def _path(path, output):
    return "${pcfiledir}/" + _relative(path, output.dirname)

def _impl(ctx):
    info = ctx.attr.dep[CcInfo]
    compilation = info.compilation_context
    if compilation.includes.to_list():
        fail("cc_build_pkgconfig mixed FDO sysroot mode supports system/external/quote includes; ordinary -I includes are not supported")
    output = ctx.actions.declare_file(ctx.label.name + "/" + ctx.attr.package_name + ".pc")
    headers = compilation.headers.to_list()
    flags = []
    for option, directories in [
        ("-isystem", compilation.system_includes.to_list() + compilation.external_includes.to_list()),
        ("-iquote", compilation.quote_includes.to_list()),
    ]:
        for directory in depset(directories).to_list():
            if any([header.path.startswith(directory + "/") for header in headers]):
                flags.append(option + _word(_path(directory, output)))
    flags.extend(["-D" + _word(value) for value in compilation.defines.to_list()])
    operands = []
    dynamic_files = {}
    for item in info.linking_context.linker_inputs.to_list():
        operands.extend([_word(value) for value in item.user_link_flags])
        for library in item.libraries:
            file = library.static_library or library.pic_static_library or library.interface_library or library.resolved_symlink_dynamic_library or library.dynamic_library
            if file:
                # Native linking collects LibraryToLink objects in a depset.
                # Collapse only an identical dynamic File; static archive repeats
                # and ordered user flags keep their existing semantics.
                if not library.static_library and not library.pic_static_library:
                    if file.path in dynamic_files:
                        continue
                    dynamic_files[file.path] = True
                if "," in file.path or "," in output.dirname:
                    fail("cc_build_pkgconfig linker File paths containing commas are not supported")
                if library.alwayslink:
                    operands.append("-Wl,--whole-archive")
                # FDO pkgconf may sysroot a bare operand after -Wl flags.
                # Keep the actual file inside an explicit linker argument.
                operands.append(_word("-Wl," + _path(file.path, output)))
                if library.alwayslink:
                    operands.append("-Wl,--no-whole-archive")
    ctx.actions.write(output, "\n".join([
        "# Build-tree interface; query with PKG_CONFIG_FDO_SYSROOT_RULES=1.",
        "Name: " + ctx.attr.package_name,
        "Description: Build-tree CcInfo interface for " + ctx.attr.package_name,
        "Version: " + ctx.attr.version,
        "Cflags: " + " ".join(flags),
        "Libs: " + " ".join(operands),
        "",
    ]))
    return [DefaultInfo(files = depset([output]))]

cc_build_pkgconfig = rule(
    implementation = _impl,
    attrs = {
        "dep": attr.label(mandatory = True, providers = [CcInfo]),
        "package_name": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
    },
    doc = "Export declared source headers/libraries without installing or copying them. Consumers also declare dep and use pkgconf FDO sysroot mode.",
)
