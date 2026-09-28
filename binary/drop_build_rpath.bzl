"""Build a binary without Bazel's build-tree rpath entries.

Bazel's `runtime_library_search_directories` feature adds $ORIGIN-relative rpath
entries pointing into `_solib_*` and `.runfiles`.

Those directories should not be in deployed artifacts.

This transition ensures that we set turn the `runtime_library_search_directories` feature off,
which drops those RPATHs.
"""

_FEATURE = "-runtime_library_search_directories"

def _drop_build_rpath_transition_impl(settings, _attr):
    features = settings["//command_line_option:features"]
    if _FEATURE in features:
        return {"//command_line_option:features": features}
    return {"//command_line_option:features": features + [_FEATURE]}

_drop_build_rpath_transition = transition(
    implementation = _drop_build_rpath_transition_impl,
    inputs = ["//command_line_option:features"],
    outputs = ["//command_line_option:features"],
)

def _drop_build_rpath_impl(ctx):
    # Just forward the src, the transition did the real work.
    return [DefaultInfo(files = depset(ctx.files.src))]

drop_build_rpath = rule(
    implementation = _drop_build_rpath_impl,
    doc = "Rebuild `src` with Bazel's build-tree rpath entries disabled.",
    attrs = {
        "src": attr.label(
            mandatory = True,
            allow_single_file = True,
            cfg = _drop_build_rpath_transition,
            doc = "The binary to rebuild without build-tree rpath entries.",
        ),
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
    },
)
