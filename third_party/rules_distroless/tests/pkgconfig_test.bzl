"""Prevent pkg-config metadata from mixing target architectures."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("@distroless_pkgconfig_regression//apt/private:translate_dependency_set.bzl", "package_deps_for_architecture")

def _pkgconfig_test_impl(ctx):
    env = unittest.begin(ctx)
    packages = {
        "trixie/libfoo-dev/amd64=1.0": {"architecture": "amd64"},
        "trixie/libfoo-dev/arm64=1.0": {"architecture": "arm64"},
        "trixie/common/all=1.0": {"architecture": "all"},
    }
    package = {"depends_on": packages.keys()}
    for arch in ["amd64", "arm64"]:
        selected = package_deps_for_architecture(packages, package, arch, "pkgconfig")
        asserts.equals(env, 2, len(selected))
        asserts.true(env, all([label.endswith("//:pkgconfig") for label in selected]))
        asserts.true(env, any([arch in label for label in selected]))
        other = "arm64" if arch == "amd64" else "amd64"
        asserts.false(env, any([other in label for label in selected]))
        data = package_deps_for_architecture(packages, package, arch)
        asserts.equals(env, [label.removesuffix("pkgconfig") + "data" for label in selected], data)
    asserts.equals(env, [], package_deps_for_architecture(packages, {"depends_on": []}, "arm64", "pkgconfig"))
    return unittest.end(env)

pkgconfig_test = unittest.make(_pkgconfig_test_impl)
