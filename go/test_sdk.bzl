"""SDK runfiles for lock contract tests, selected by the consuming root."""

_GO_TOOLCHAIN = "@io_bazel_rules_go//go:toolchain"

def _test_sdk_impl(ctx):
    sdk = ctx.toolchains[_GO_TOOLCHAIN].sdk
    files = depset(
        [sdk.root_file, sdk.go, sdk.package_list],
        transitive = [sdk.tools, sdk.libs, sdk.headers, sdk.srcs],
    )
    return [DefaultInfo(
        files = depset([sdk.root_file]),
        runfiles = ctx.runfiles(transitive_files = files),
    )]

test_sdk = rule(
    implementation = _test_sdk_impl,
    toolchains = [_GO_TOOLCHAIN],
)
