"""Assemble a Debian sysroot from explicitly selected package data archives."""

def _debian_sysroot_impl(rctx):
    # Resolve all labels before extracting, so a repository restart cannot
    # partially overlay an earlier extraction. Later archives take precedence.
    archives = [rctx.path(package) for package in rctx.attr.packages]
    for archive in archives:
        rctx.extract(archive)

    # Debian is usrmerged. These aliases also let the target dynamic loader
    # resolve its normal /lib paths when this tree is used with QEMU -L.
    for directory in ["bin", "lib", "sbin"]:
        if not rctx.path(directory).exists and rctx.path("usr/" + directory).exists:
            link = rctx.execute(["ln", "-s", "usr/" + directory, directory])
            if link.return_code != 0:
                fail("Could not create relative usrmerge alias {}: {}".format(
                    directory,
                    link.stderr,
                ))

    absolute_links = rctx.execute(["find", "usr", "-type", "l", "-lname", "/*", "-print"])
    if absolute_links.return_code != 0 or absolute_links.stdout:
        fail("The Debian sysroot contains unexpected absolute symlinks: {}{}".format(
            absolute_links.stdout,
            absolute_links.stderr,
        ))

    rctx.file(".sysroot-root", "Debian sysroot assembled by sonic-build-infra\n")
    rctx.file("BUILD.bazel", """\
package(default_visibility = ["//visibility:public"])
exports_files([".sysroot-root"])
filegroup(
    name = "files",
    srcs = glob(["usr/**", "bin/**", "lib/**", "sbin/**"]),
)
""")

debian_sysroot = repository_rule(
    implementation = _debian_sysroot_impl,
    attrs = {
        "packages": attr.label_list(
            allow_files = [".tar.xz"],
            mandatory = True,
            doc = "Ordered Debian package data archives; later archives overlay earlier ones.",
        ),
    },
    doc = "Assemble package archives into a sysroot exposing :files and :.sysroot-root. Package versions and architecture are selected by the caller.",
)
