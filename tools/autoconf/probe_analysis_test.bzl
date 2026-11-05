"""Header reachability cases independent of any FRR include layout."""
def _native_fixture(ctx):
    return [DefaultInfo(files = ctx.attr.probe[DefaultInfo].files)]

native_fixture = rule(
    implementation = _native_fixture,
    attrs = {"probe": attr.label(cfg = "exec", mandatory = True)},
)
