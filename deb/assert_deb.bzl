"""Assertion helpers for `.deb` archives.

Uses the hermetic tar from `tar.bzl` to read `ar` archives.
Mirrors tar/assert_tar.bzl.
"""

load("@bazel_lib//lib:diff_test.bzl", "diff_test")
load("@rules_python//python:defs.bzl", "py_test")

_TAR_TOOLCHAIN = "@tar.bzl//tar/toolchain:type"

def assert_deb_members(name, deb, expected):
    """Assert the ar member list (and order) of a .deb.

    A valid .deb must contain `debian-binary`, `control.tar.gz`, `data.tar.gz`
    in that exact order (dpkg requires `debian-binary` first).

    Args:
        name: name of the test target.
        deb: label of the .deb archive.
        expected: golden member listing, one name per line, in archive order.
    """
    members = "{}_members".format(name)
    native.genrule(
        name = members,
        srcs = [deb],
        testonly = True,
        outs = ["{}.members".format(name)],
        cmd = "$(BSDTAR_BIN) -tf $(execpath {deb}) >$@".format(deb = deb),
        toolchains = [_TAR_TOOLCHAIN],
    )
    diff_test(
        name = name,
        file1 = members,
        file2 = expected,
        timeout = "short",
    )

def assert_deb_data(name, deb, expected):
    """Assert the file listing of a .deb's inner data.tar.gz.

    Args:
        name: name of the test target.
        deb: label of the .deb archive.
        expected: golden listing, one entry per line.
            Must be produced with `bsdtar -tvf <inner data> | LC_ALL=C sort --key=9`,
            same as assert_tar.
    """
    listing = "{}_listing".format(name)
    native.genrule(
        name = listing,
        srcs = [deb],
        testonly = True,
        outs = ["{}.listing".format(name)],
        cmd = ("$(BSDTAR_BIN) -xOf $(execpath {deb}) data.tar.gz | " +
               "$(BSDTAR_BIN) --verbose --list --file - | " +
               "LC_ALL=C sort --key=9 >$@").format(deb = deb),
        toolchains = [_TAR_TOOLCHAIN],
    )
    diff_test(
        name = name,
        file1 = listing,
        file2 = expected,
        timeout = "short",
    )

def assert_deb_md5sums(name, deb, expected):
    """Assert the text of a .deb's control `md5sums` file.

    Args:
        name: name of the test target.
        deb: label of the .deb archive.
        expected: golden `md5sums` contents.
    """
    md5sums = "{}_md5sums".format(name)
    native.genrule(
        name = md5sums,
        srcs = [deb],
        testonly = True,
        outs = ["{}.md5sums".format(name)],
        cmd = ("$(BSDTAR_BIN) -xOf $(execpath {deb}) control.tar.gz | " +
               "$(BSDTAR_BIN) -xOf - ./md5sums >$@").format(deb = deb),
        toolchains = [_TAR_TOOLCHAIN],
    )
    diff_test(
        name = name,
        file1 = md5sums,
        file2 = expected,
        timeout = "short",
    )

def assert_deb_control(name, deb, expected):
    """Assert the fields of a .deb's control file independent of field order.

    Args:
        name: name of the test target.
        deb: label of the .deb archive.
        expected: golden `control` file contents.
    """
    control = "{}_control".format(name)
    native.genrule(
        name = control,
        srcs = [deb],
        testonly = True,
        outs = ["{}.control".format(name)],
        cmd = ("$(BSDTAR_BIN) -xOf $(execpath {deb}) control.tar.gz | " +
               "$(BSDTAR_BIN) -xOf - ./control >$@").format(deb = deb),
        toolchains = [_TAR_TOOLCHAIN],
    )
    expected_target = name + "_expected"
    native.alias(name = expected_target, actual = expected, testonly = True)
    py_test(
        name = name,
        srcs = [Label("//deb:assert_control.py")],
        main = Label("//deb:assert_control.py"),
        args = ["$(location %s)" % control, "$(location %s)" % expected_target],
        data = [control, expected_target],
        deps = [Label("//apt:lib")],
        timeout = "short",
    )
