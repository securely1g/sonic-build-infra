def _impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name)
    ctx.actions.run_shell(outputs = [out], arguments = [out.path], command = 'mkdir -p "$1"; printf fixture > "$1/marker"')
    return [DefaultInfo(files = depset([out]))]
fixture_base = rule(implementation = _impl)
