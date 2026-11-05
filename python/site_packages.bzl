"""Extensions to tar.bzl to handle site_packages."""

load("@bazel_skylib//rules:write_file.bzl", "write_file")
load("@rules_python//python:defs.bzl", "PyInfo")
load("@tar.bzl", "tar", _mutate = "mutate")
load(":source_package.bzl", "source_package_install", "source_package_runtime")

def _export_pyinfo(ctx):
    files = []
    for dep in ctx.attr.srcs:
        files.append(dep[PyInfo].transitive_sources)
    return DefaultInfo(files = depset([], transitive = files))

export_py_info = rule(
    implementation = _export_pyinfo,
    doc = "Export `PyInfo.transitive_sources` as DefaultInfo.",
    attrs = {
        "srcs": attr.label_list(
            providers = [PyInfo],
        ),
    },
)

def _legacy_site_packages(name, srcs, mutate = None, **kwargs):
    """Conveninece macro to create a tar with a bunch of python dependencies.

    srcs must export the required files with `PyInfo`.

    Args:
        name: Target name (also used as the name of the tar).
        srcs: List of targets that export `PyInfo` (e.g. `py_library` targets).
        mutate: Not allowed. If you want to use `mutate`, chances are you're better off using `export_py_info` and `tar` directly.
        **kwargs: Additional keyword arguments forwarded to the underlying `tar` rule.
    """
    if mutate != None:
        fail("mutate is not allowed in site_packages. If you want to use `mutate`, chances are you're better off using `export_py_info` and `tar` directly.")

    export_py_info(
        name = name + "_info",
        srcs = srcs,
    )
    script_name = name + "_awk"
    write_file(
        name = script_name,
        out = name + ".awk",
        content = """
@include "default"

# Skip install directories that we don't need.
# Keeping these in causes duplicate path conflicts in several versions of docker.
/^(install\\/|install\\/lib\\/|install\\/lib\\/python3.11\\/) uid/ {
  next
}

# Skip the install/bin/ directory entry itself.
/^install\\/bin\\/ / {
  next
}

{
  # Place binaries from install/bin/ under ./usr/bin/.
  if (sub("install/bin/", "./usr/bin/")) {
    # matched and replaced, done
  } else {
    # Remove prefixes for third party dependencies.
    sub("install/lib/python3.11/site-packages/", "");

    # Add everything, first and third party code, into ./usr/lib/python3/dist-packages.
    # We sohuld abstract this into the interface of site_packages when we have the need to.
    sub(/^/, "./usr/lib/python3/dist-packages/")
  }
}
""".split("\n"),
    )
    tar(
        name = name,
        srcs = [":" + name + "_info"],
        mutate = _mutate(
            awk_script = script_name,
        ),
        **kwargs
    )

def site_packages(name, srcs = [], distribution = None, version = None, files = {}, runtime_deps = [], **kwargs):
    """Build this distribution's installation TAR and export runtime metadata.

    With distribution/version, files maps single-file labels to package-relative
    paths (including resources, extensions and .data schemes). regular_file_mode
    optionally fixes declared payload files to 0644 or 0755; generated entry points
    remain executable. An empty value preserves the input executable status. Metadata attributes
    use the supported py_wheel names and may share a component declaration. runtime_deps links
    other source distributions; it never adds their files to this package's TAR.
    Calls without distribution retain the legacy PyInfo source aggregation API.

    Args:
        name: Public installation target; its default output is name.tar.
        srcs: Legacy PyInfo aggregation inputs, used without distribution.
        distribution: Distribution name; enables explicit package installation.
        version: Normalized distribution version, shared with the wheel.
        files: Single-file labels mapped to package-relative installation paths.
        runtime_deps: Other source installation targets providing PythonRuntimeInfo.
        **kwargs: Metadata and target installation paths; legacy calls forward tar options.
    """
    if distribution == None:
        if version != None or files or runtime_deps:
            fail("distribution is required for explicit package installation")
        _legacy_site_packages(name = name, srcs = srcs, **kwargs)
        return
    if srcs:
        fail("use files for explicit package contents; srcs is the legacy PyInfo API")
    if version == None:
        fail("version is required for explicit package installation")
    common = {key: kwargs.pop(key) for key in ["visibility", "testonly", "tags", "compatible_with", "target_compatible_with"] if key in kwargs}
    source_package_install(
        name = name + "_install",
        distribution = distribution,
        version = version,
        files = files,
        **dict(kwargs, **common)
    )
    native.filegroup(
        name = name + "_mtree",
        srcs = [":" + name + "_install"],
        output_group = "mtree",
        **common
    )
    tar(
        name = name + "_tar",
        out = name + ".tar",
        srcs = [":" + name + "_install"],
        mtree = ":" + name + "_mtree",
        compress = None,
        **common
    )
    source_package_runtime(
        name = name,
        tar = ":" + name + "_tar",
        installation = ":" + name + "_install",
        runtime_deps = runtime_deps,
        **common
    )
