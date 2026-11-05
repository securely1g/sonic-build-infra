"""Integration test entry using an explicitly declared fixed uv executable."""

load("@rules_python//python:py_test.bzl", "py_test")

def runtime_lock_update_test(name, uv, **kwargs):
    """Run real resolver tests using a Bazel-managed uv artifact.

    Args:
        name: Test target name.
        uv: Label of a fixed uv 0.12.18 executable for the execution host.
        **kwargs: Common py_test attributes.
    """
    py_test(
        name = name,
        srcs = [Label(":runtime_lock_update_test.py")],
        main = Label(":runtime_lock_update_test.py"),
        args = ["--uv", "$(rootpath %s)" % uv],
        data = [uv],
        deps = [Label("//python:runtime_lock_lib"), Label(":runtime_lock_test_support")],
        python_version = "3.11",
        **kwargs
    )
