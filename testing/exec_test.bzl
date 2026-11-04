"""Run an execution-platform checker against target-platform artifacts."""

load("@bazel_skylib//lib:shell.bzl", "shell")

def _runfile(ctx, file):
    return file.short_path[3:] if file.short_path.startswith("../") else ctx.workspace_name + "/" + file.short_path

def _impl(ctx):
    files = ctx.files.srcs + ctx.files.data
    by_location = {file.path: file for file in files}
    for file in files:
        # expand_location shell-quotes paths containing spaces/apostrophes.
        by_location[shell.quote(file.path)] = file
    arguments = ['"$TEST_SRCDIR"/' + shell.quote(_runfile(ctx, file)) for file in ctx.files.srcs]
    for value in ctx.attr.checker_args:
        parts = value.split("$(location ")
        fragments = [shell.quote(parts[0])]
        for part in parts[1:]:
            if ")" not in part:
                fail("Unterminated $(location ...) in argument: " + value)
            label, literal = part.split(")", 1)
            path = ctx.expand_location("$(location " + label + ")", targets = ctx.attr.srcs + ctx.attr.data)
            if path not in by_location:
                fail("$(location ...) must resolve to one declared srcs/data file: " + label)
            fragments.append('"$TEST_SRCDIR"/' + shell.quote(_runfile(ctx, by_location[path])))
            fragments.append(shell.quote(literal))
        arguments.append("".join(fragments))
    executable = ctx.actions.declare_file(ctx.label.name + ".sh")
    command = 'exec "$TEST_SRCDIR"/' + shell.quote(_runfile(ctx, ctx.executable.runner)) + " " + " ".join(arguments)
    ctx.actions.write(executable, "#!/bin/sh\nset -eu\n" + command + "\n", is_executable = True)
    runfiles = ctx.runfiles(files = files + [ctx.executable.runner])
    for dependency in ctx.attr.srcs + ctx.attr.data + [ctx.attr.runner]:
        runfiles = runfiles.merge(dependency[DefaultInfo].default_runfiles).merge(dependency[DefaultInfo].data_runfiles)
    return [DefaultInfo(executable = executable, runfiles = runfiles)]

exec_test = rule(
    implementation = _impl,
    test = True,
    attrs = {
        "runner": attr.label(executable = True, cfg = "exec", mandatory = True),
        "srcs": attr.label_list(allow_files = True, doc = "Ordered target files passed as leading positional arguments."),
        "data": attr.label_list(allow_files = True),
        "checker_args": attr.string_list(doc = "One literal argument per item, with optional single-file $(location label) substitutions."),
    },
)
