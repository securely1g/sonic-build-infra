"""Expose a native shared library to consumers that require CcInfo."""

load("@rules_cc//cc:defs.bzl", "CcInfo", "cc_common")
load("@rules_cc//cc/common:cc_shared_library_info.bzl", "CcSharedLibraryInfo")

def _runtime_search_flags(flags):
    """Retain search options without turning linker arguments into GCC options."""
    result = []
    for index, flag in enumerate(flags):
        if flag == "-Xlinker":
            if index + 1 < len(flags):
                argument = flags[index + 1]
                if argument.startswith("-rpath-link=") or argument.startswith("-L"):
                    result.extend([flag, argument])
        elif index and flags[index - 1] == "-Xlinker":
            # The preceding option owns this argument, including ignored flags.
            continue
        elif flag.startswith("-Wl,-rpath-link") or flag.startswith("-L"):
            result.append(flag)
    return result

def _reown_linker_input(item, owner, extra_flags = [], extra_inputs = []):
    """Re-own forwarded LinkerInput under owner so dynamic_deps filtering keeps it."""
    return cc_common.create_linker_input(
        owner = owner,
        libraries = depset(item.libraries),
        # A depset loses repeated -Xlinker tokens and breaks option pairs.
        user_link_flags = list(item.user_link_flags) + extra_flags,
        additional_inputs = depset(list(item.additional_inputs) + extra_inputs),
    )

def _shared_library_cc_info_impl(ctx):
    library = ctx.attr.shared_library
    shared = library[CcSharedLibraryInfo]

    # Reuse the producer's LibraryToLink objects and solib paths. Creating a
    # new library or importing its output would relocate the ELF again.
    # Re-own forwarded inputs under ctx.label so rules_cc does not drop them
    # when a consumer binary declares dynamic_deps.
    # The producer's linker_input also carries precompiled dynamic libraries.
    runtime_flags = []
    runtime_inputs = []
    for dependency in ctx.attr.runtime_deps:
        for item in dependency[CcInfo].linking_context.linker_inputs.to_list():
            runtime_flags.extend(_runtime_search_flags(item.user_link_flags))
            runtime_inputs.extend(item.additional_inputs)
    linker_input = _reown_linker_input(
        shared.linker_input,
        ctx.label,
        extra_flags = runtime_flags,
        extra_inputs = runtime_inputs,
    )
    linker_inputs = [linker_input] + [
        _reown_linker_input(dependency.linker_input, ctx.label)
        for dependency in shared.dynamic_deps.to_list()
    ]
    return [
        library[DefaultInfo],
        CcInfo(
            linking_context = cc_common.create_linking_context(
                linker_inputs = depset(linker_inputs, order = "topological"),
            ),
        ),
    ]

shared_library_cc_info = rule(
    implementation = _shared_library_cc_info_impl,
    doc = """Adapt cc_shared_library for C++, Go cgo, and Rust CcInfo consumers.

Preserves the producer's files, runfiles, and native shared-library linker inputs,
including transitive dynamic_deps. It does not copy the ELF, import it again, or
reintroduce the producer's static archives. Add headers through a cc_library
depending on this target; use the original producer for native dynamic_deps.
""",
    attrs = {
        "runtime_deps": attr.label_list(
            providers = [CcInfo],
            doc = "Forward legacy search flags, ordered -Xlinker pairs with joined -L<dir> or -rpath-link=<dir> arguments, and declared linker inputs; no static archives or additional direct libraries.",
        ),
        "shared_library": attr.label(
            mandatory = True,
            providers = [CcSharedLibraryInfo],
            doc = "The native cc_shared_library producer to expose as CcInfo.",
        ),
    },
    provides = [CcInfo],
)
