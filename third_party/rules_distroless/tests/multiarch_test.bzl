"""Regression for architecture-specific headers in declared Debian packages."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("@distroless_compatibility_regression//apt/private:multiarch.bzl", "default_include_dirs")

def _multiarch_test_impl(ctx):
    env = unittest.begin(ctx)
    for triplet in ["x86_64-linux-gnu", "aarch64-linux-gnu", "arm-linux-gnueabihf"]:
        headers = [
            "usr/include/lua5.1/lua.h",
            "usr/include/{}/lua5.1-deb-multiarch.h".format(triplet),
            "usr/include/{}/another.inc".format(triplet),
        ]
        asserts.equals(env, ["/usr/include", "/usr/include/" + triplet], default_include_dirs(headers))
    asserts.equals(env, ["/usr/include"], default_include_dirs([
        "usr/include/project/header.h",
        "usr/share/examples/aarch64-linux-gnu/example.h",
        "opt/include/x86_64-linux-gnu/other.h",
    ]))
    asserts.equals(env, ["/usr/include"], default_include_dirs([]))
    return unittest.end(env)

multiarch_test = unittest.make(_multiarch_test_impl)
