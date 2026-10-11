"""Resolve actual package index records without downloading package payloads."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("@distroless_compatibility_regression//apt/private:apt_deb_repository.bzl", deb_repository = "DO_NOT_DEPEND_ON_THIS_TEST_ONLY")
load("@distroless_compatibility_regression//apt/private:apt_dep_resolver.bzl", "dependency_resolver")

def _index(arch):
    repository = deb_repository.new()
    for name, version, architecture, provides, suite in [
        ("vim", "2:9.1.1230-2", arch, "", "trixie"),
        ("vim-nox", "2:9.1.1230-2", arch, "vim (= 2:9.1.1230-2), editor", "trixie"),
        ("vim", "2:9.0.0000-1", arch, "", "oldstable"),
        ("vim-old-provider", "2:9.1.1230-2", arch, "vim (= 2:9.1.1230-2)", "oldstable"),
        ("all-provider", "1", "all", "virtual-only (= 3)", "trixie"),
        ("all-concrete", "1", "all", "", "trixie"),
        ("arch-provider", "9", arch, "all-concrete (= 1)", "trixie"),
    ]:
        repository.parse_repository("\n".join([
            "Package: " + name,
            "Version: " + version,
            "Architecture: " + architecture,
            "Provides: " + provides,
            "Priority: optional",
        ]), dist = suite)
    return dependency_resolver.new(repository)

def _concrete_packages_test_impl(ctx):
    env = unittest.begin(ctx)
    for arch in ["amd64", "arm64"]:
        resolver = _index(arch)
        for name, constraint, suite, expected in [
            # An explicit concrete name wins, both with and without an exact pin.
            ("vim", None, "trixie", "vim"),
            ("vim", ("=", "2:9.1.1230-2"), "trixie", "vim"),
            # A concrete candidate must satisfy the version and suite selection.
            ("vim", ("=", "2:9.1.1230-2"), "oldstable", "vim-old-provider"),
            ("vim", ("=", "2:9.0.0000-1"), "oldstable", "vim"),
            ("vim", ("=", "999"), "trixie", None),
            # Virtual-only and Architecture: all packages retain their behavior.
            ("editor", None, "trixie", "vim-nox"),
            ("virtual-only", ("=", "3"), "trixie", "all-provider"),
            ("all-concrete", ("=", "1"), "trixie", "all-concrete"),
        ]:
            package, warning = resolver.resolve_package(
                name = name,
                version = constraint,
                arch = arch,
                suites = [suite],
            )
            asserts.equals(
                env,
                expected,
                package["Package"] if package else None,
                "{} {} {} {}".format(arch, name, constraint, suite),
            )
            asserts.equals(env, None, warning)
    return unittest.end(env)

concrete_packages_test = unittest.make(_concrete_packages_test_impl)
