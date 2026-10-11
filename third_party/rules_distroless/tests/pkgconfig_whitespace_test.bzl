"""Regression for real pkg-config whitespace and optional empty substitutions."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("@distroless_compatibility_regression//apt/private:pkgconfig.bzl", "parse_pc", "process_pcconfig")

def _pkgconfig_whitespace_test_impl(ctx):
    env = unittest.begin(ctx)
    pc = """prefix=/usr
libdir=${prefix}/lib
includedir=${prefix}/include
Name: example
Libs: -L${libdir}  -lexample ${optional} \t -pthread
Libs.private:  -lprivate\t-lsecond
Cflags: -I${includedir}\t-DFEATURE=1  -DOTHER
"""
    result = process_pcconfig(parse_pc(pc))
    asserts.equals(env, ["libexample", "libprivate", "libsecond"], result[0])
    asserts.equals(env, ["-Wl,-rpath=/usr/lib", "-pthread"], result[3])
    asserts.equals(env, ["/usr/lib"], result[4])
    asserts.equals(env, ["/usr/include"], depset(result[5]).to_list())
    asserts.equals(env, ["FEATURE=1", "OTHER"], result[6])
    for whitespace in ["", " ", "  \t  "]:
        empty = process_pcconfig(({"Libs": whitespace, "Libs.private": whitespace, "Cflags": whitespace}, {}))
        asserts.equals(env, [], empty[0])
        asserts.equals(env, [], empty[3])
        asserts.equals(env, [], empty[4])
        asserts.equals(env, [], empty[6])
    return unittest.end(env)

pkgconfig_whitespace_test = unittest.make(_pkgconfig_whitespace_test_impl)
