"""Assertion that a shared library was built from position-independent objects.

Mirrors //binary:assert_stripped.
"""

load("//toolchains/binutils:binutils_toolchain.bzl", "BINUTILS_TOOLCHAIN_TYPE")

def _assert_position_independent_test_impl(ctx):
    binutils = ctx.toolchains[BINUTILS_TOOLCHAIN_TYPE].binutils
    readelf = binutils.readelf.executable

    library = ctx.file.library
    test_script = ctx.file._test_script

    exe = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.symlink(output = exe, target_file = test_script, is_executable = True)

    env = {
        "READELF": readelf.short_path,
        "LIBRARY_UNDER_TEST": library.short_path,
    }
    runfiles = ctx.runfiles(files = [library, readelf])
    if binutils.runfiles["readelf"]:
        runfiles = runfiles.merge(binutils.runfiles["readelf"])
    return [
        DefaultInfo(
            executable = exe,
            runfiles = runfiles,
        ),
        RunEnvironmentInfo(environment = env),
    ]

_assert_position_independent_test = rule(
    implementation = _assert_position_independent_test_impl,
    test = True,
    toolchains = [BINUTILS_TOOLCHAIN_TYPE],
    attrs = {
        "library": attr.label(
            mandatory = True,
            allow_single_file = True,
            doc = "The shared library (.so) to check.",
        ),
        "_test_script": attr.label(
            default = "//binary:test_position_independent.sh",
            allow_single_file = True,
            doc = "The shell script holding the actual assertions.",
        ),
    },
)

def assert_position_independent(name, library, **kwargs):
    """Assert that `library` was linked from position-independent objects.

    Args:
        name: name of the test target.
        library: label of the shared library (.so) to check.
        **kwargs: passed through to the underlying test (e.g. tags, timeout).
    """
    _assert_position_independent_test(
        name = name,
        library = library,
        **kwargs
    )
