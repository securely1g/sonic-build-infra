"""Check the configured source graph of the public deployment macros."""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")

_DebugGraph = provider(fields = ["nodes"])

def _graph_impl(target, ctx):
    nodes = [(str(target.label), ctx.bin_dir.path, ctx.rule.kind)]
    for name in ["src", "binaries"]:
        value = getattr(ctx.rule.attr, name, [])
        dependencies = value if type(value) == "list" else [value]
        for dependency in dependencies:
            if type(dependency) == "Target" and _DebugGraph in dependency:
                nodes += dependency[_DebugGraph].nodes
    return [_DebugGraph(nodes = nodes)]

_graph = aspect(implementation = _graph_impl, attr_aspects = ["src", "binaries"])

def _test_impl(ctx):
    env = analysistest.begin(ctx)
    nodes = analysistest.target_under_test(env)[_DebugGraph].nodes
    wrappers = [label for label, _, kind in nodes if kind == "keep_debug_info"]
    assert_message = "Configured source graph: " + str(nodes)
    asserts.equals(env, ctx.attr.expected_wrappers, len(wrappers), assert_message)
    sources = [directory for label, directory, _ in nodes if label.endswith(":probe")]
    asserts.equals(env, 1, len(sources), assert_message)
    if len(sources) == 1:
        asserts.equals(env, ctx.attr.expected_wrappers > 0, sources[0] != nodes[0][1], assert_message)
    return analysistest.end(env)

debug_graph_test = analysistest.make(
    _test_impl,
    attrs = {"expected_wrappers": attr.int(mandatory = True)},
    extra_target_under_test_aspects = [_graph],
)
