"""Tiny package archives exercise repository assembly across module boundaries."""

def _fixture_packages_impl(rctx):
    for package, value in [("first", "first"), ("second", "second")]:
        rctx.file(package + "/usr/include/order.h", value, executable = False)
        for directory in ["bin", "lib", "sbin"]:
            rctx.file(package + "/usr/" + directory + "/payload", value, executable = False)
    rctx.file("absolute/usr/include/placeholder", "", executable = False)
    result = rctx.execute(["ln", "-s", "/outside", "absolute/usr/include/escape"])
    if result.return_code:
        fail(result.stderr)
    for package in ["first", "second", "absolute"]:
        result = rctx.execute(["tar", "-cJf", package + ".tar.xz", "-C", package, "usr"])
        if result.return_code:
            fail(result.stderr)
    rctx.file("BUILD.bazel", 'exports_files(["first.tar.xz", "second.tar.xz", "absolute.tar.xz"])')

fixture_packages = repository_rule(implementation = _fixture_packages_impl)
