"""Check target width, tool configuration, headers and the public output providers."""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")
load("@sonic_build_infra//swig:defs.bzl", "swig_gen", "swig_gen_go")

def _tree_impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name)
    ctx.actions.run_shell(outputs = [out], command = 'mkdir -p "$1"; touch "$1/swig.swg"', arguments = [out.path])
    return [DefaultInfo(files = depset([out]))]

swig_tree = rule(implementation = _tree_impl)

def _swig_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)
    asserts.equals(env, 1, len(actions))
    action = actions[0]
    argv = action.argv
    asserts.equals(env, ctx.attr.wordsize == 64, "-DSWIGWORDSIZE64" in argv)
    asserts.true(env, "-DEXAMPLE=7" in argv)
    asserts.true(env, "-DFEATURE" in argv)
    if ctx.attr.language == "go":
        asserts.equals(env, str(ctx.attr.wordsize), argv[argv.index("-intgosize") + 1])
    else:
        asserts.true(env, "-python" in argv)
    inputs = [f.basename for f in action.inputs.to_list()]
    asserts.true(env, "transitive.h" in inputs)
    asserts.true(env, "direct.h" in inputs)
    asserts.true(env, "binding.i" in inputs)

    # SWIG resolves under the execution configuration even for a target width of 32.
    asserts.true(env, "-exec-" in argv[0])
    asserts.true(env, action.env["SWIG_LIB"].endswith("tree_lib") if ctx.attr.tree else not action.env["SWIG_LIB"].endswith("tree_lib"))
    target = analysistest.target_under_test(env)
    outputs = target[DefaultInfo].files.to_list()
    groups = target[OutputGroupInfo]
    if ctx.attr.language == "go":
        grouped = groups.go.to_list() + groups.cxx.to_list() + groups.hdr.to_list()
    else:
        grouped = groups.cpp.to_list() + groups.python.to_list()
    asserts.equals(env, sorted([f.path for f in outputs]), sorted([f.path for f in grouped]))
    asserts.equals(env, 3 if ctx.attr.language == "go" else 2, len(outputs))
    return analysistest.end(env)

swig_test = analysistest.make(
    _swig_test_impl,
    attrs = {"wordsize": attr.int(), "language": attr.string(), "tree": attr.bool()},
)

def swig_cases():
    """Instantiate the Python/Go, target-width, and library-layout test matrix."""
    for language in ["python", "go"]:
        for width in [32, 64]:
            for tree in [False, True]:
                name = "{}_{}_{}".format(language, width, "tree" if tree else "files")
                kwargs = dict(
                    name = name,
                    interface = "binding.i",
                    hdrs = ["direct.h"],
                    deps = [":transitive"],
                    defines = ["EXAMPLE=7", "FEATURE"],
                    swig_lib = ":tree_lib" if tree else ":files_lib",
                    tags = ["manual"],
                )

                # Omitting 64 explicitly tests the backwards-compatible default.
                if width == 32:
                    kwargs["wordsize"] = width
                if language == "python":
                    swig_gen(cpp_out = name + "/binding_wrap.cpp", python_out = name + "/binding.py", **kwargs)
                else:
                    swig_gen_go(cxx_out = name + "/binding_wrap.cxx", go_out = name + "/binding.go", hdr_out = name + "/binding_wrap.h", **kwargs)
                swig_test(name = name + "_test", target_under_test = ":" + name, wordsize = width, language = language, tree = tree)
