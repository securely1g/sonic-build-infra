"""Prepare fixed archives and ordered patch stages with repository builtins."""

def _path(value, description, allow_empty = False):
    if type(value) != "string" or (not value and not allow_empty):
        fail(description + " must be a relative path")
    if value and (value.startswith("/") or "\\" in value or
                  any([part in ["", ".", ".."] for part in value.split("/")])):
        fail(description + " must be a relative path without empty, '.' or '..' components: " + value)
    return value

def _keys(step, required, optional):
    if type(step) != "dict":
        fail("source step must be a JSON object")
    for key in required:
        if key not in step:
            fail("source step is missing required key: " + key)
    for key in step:
        if key not in required + optional:
            fail("unknown source step key: " + key)

def _input(ctx, name):
    _path(name, "input name")
    if name not in ctx.attr.inputs:
        fail("source input is not declared: " + name)
    return ctx.path(ctx.attr.inputs[name])

def _local(ctx, name, allow_empty = False):
    _path(name, "repository path", allow_empty)
    # An earlier archive may contain symlinks. Its paths must not redirect a
    # later patch, read or extraction outside this repository.
    root_path = ctx.path(".")
    root = str(root_path.realpath if root_path.exists else root_path)
    for end in range(1, len(name.split("/")) + 1):
        path = ctx.path("/".join(name.split("/")[:end]))
        if path.exists:
            resolved = str(path.realpath)
            if resolved != root and not resolved.startswith(root + "/"):
                fail("repository path escapes the source repository: " + name)
    return ctx.path(name or ".")

def _strip(step):
    value = step.get("strip", 1)
    if type(value) != "int" or value < 0:
        fail("patch strip must be a non-negative integer")
    return value

def _hunk_count(value, prefix):
    if not value.startswith(prefix):
        fail("invalid unified hunk range: " + value)
    pieces = value[1:].split(",")
    if len(pieces) not in [1, 2] or any([not part or any([c not in "0123456789" for c in part.elems()]) for part in pieces]):
        fail("invalid unified hunk range: " + value)
    return int(pieces[1]) if len(pieces) == 2 else 1

def _unified_sections(content):
    # Count hunk payload lines before recognizing another file header. A
    # deleted '-- ...' followed by an added '++ ...' is payload, not a boundary.
    lines = content.split("\n")
    sections = []
    start = -1
    new_header = -1
    has_hunk = False
    old_remaining, new_remaining = 0, 0
    for index in range(len(lines)):
        line = lines[index]
        if old_remaining or new_remaining:
            if line.startswith(" "):
                old_remaining -= 1
                new_remaining -= 1
            elif line.startswith("-"):
                old_remaining -= 1
            elif line.startswith("+"):
                new_remaining -= 1
            elif line == "\\ No newline at end of file":
                continue
            else:
                fail("truncated unified hunk at line " + str(index + 1))
            if old_remaining < 0 or new_remaining < 0:
                fail("unified hunk line counts disagree at line " + str(index + 1))
            continue
        if index == new_header:
            continue
        if any([line.startswith(prefix) for prefix in ["diff --git ", "diff --cc ", "diff --combined ", "GIT binary patch", "Binary files ", "old mode ", "new mode ", "new file mode ", "deleted file mode ", "rename from ", "rename to ", "copy from ", "copy to "]]):
            fail("Git metadata and binary patches require format=native")
        if line.startswith("--- ") or line.startswith("---\t"):
            if index + 1 >= len(lines) or not (lines[index + 1].startswith("+++ ") or lines[index + 1].startswith("+++\t")):
                fail("unified old file header must be followed by a new file header")
            if start >= 0:
                if not has_hunk:
                    fail("unified file section has no hunk")
                sections.append("\n".join(lines[start:index]) + "\n")
            start = index
            new_header = index + 1
            has_hunk = False
        elif line.startswith("@@ "):
            fields = [word for word in line.split(" ") if word]
            if start < 0 or len(fields) < 4 or fields[3] != "@@":
                fail("invalid unified hunk header at line " + str(index + 1))
            old_remaining = _hunk_count(fields[1], "-")
            new_remaining = _hunk_count(fields[2], "+")
            has_hunk = True
        elif start >= 0 and has_hunk and line and line[0] in [" ", "+", "-"]:
            fail("unified hunk line counts disagree at line " + str(index + 1))
    if old_remaining or new_remaining:
        fail("truncated unified hunk at end of patch")
    if start < 0 or not has_hunk:
        fail("unified patch requires file headers and a hunk")
    sections.append("\n".join(lines[start:]))
    return sections

def _format(step):
    value = step.get("format", "native")
    if value not in ["native", "unified"]:
        fail("patch format must be native or unified")
    return value

def _patch(ctx, patch, strip, patch_format):
    if patch_format == "native":
        ctx.patch(patch, strip = strip)
        return
    sections = _unified_sections(ctx.read(patch))
    scratch = ".prepared_source_unified_patch"
    if ctx.path(scratch).exists:
        fail("source archive conflicts with reserved patch scratch path: " + scratch)
    for index, section in enumerate(sections):
        path = scratch + "/" + str(index) + ".patch"
        ctx.file(path, section, executable = False)
        ctx.patch(path, strip = strip)
    ctx.delete(scratch)

def _series(ctx, step):
    _keys(step, ["op"], ["series", "series_input", "patch_inputs", "strip", "format"])
    patch_format = _format(step)
    external = "series_input" in step
    if external == ("series" in step):
        fail("patch_series requires exactly one of series or series_input")
    if external:
        if type(step.get("patch_inputs")) != "list":
            fail("series_input requires an ordered patch_inputs list")
        series_name = _path(step["series_input"], "series input")
        series = _input(ctx, series_name)
    else:
        if "patch_inputs" in step:
            fail("patch_inputs is only valid with series_input")
        series_name = _path(step["series"], "series path")
        series = _local(ctx, series_name)
    directory = series_name.rpartition("/")[0]
    patches = []
    for line in ctx.read(series).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = [word for word in line.replace("\t", " ").split(" ") if word]
        if len(words) != 1 or words[0].startswith("-"):
            fail("unsupported patch series arguments: " + line)
        relative = _path(words[0], "series patch")
        name = directory + "/" + relative if directory else relative
        if name in patches:
            fail("duplicate patch series path: " + name)
        patches.append(name)
    if external and patches != step["patch_inputs"]:
        fail("patch_inputs must match the ordered full relative series paths: " + str(patches))
    strip = _strip(step)
    for name in patches:
        _patch(ctx, _input(ctx, name) if external else _local(ctx, name), strip, patch_format)

def _prepared_source_impl(ctx):
    for name in ctx.attr.inputs:
        _input(ctx, name)
    for encoded in ctx.attr.steps:
        step = json.decode(encoded)
        if type(step) != "dict" or "op" not in step:
            fail("source step requires an op")
        op = step["op"]
        if op == "archive":
            _keys(step, ["op", "urls", "sha256"], ["strip_prefix", "output"])
            urls, sha256 = step["urls"], step["sha256"]
            if type(urls) != "list" or not urls or any([type(url) != "string" or not url for url in urls]):
                fail("archive urls must be a non-empty list of URLs")
            if type(sha256) != "string" or len(sha256) != 64 or any([c not in "0123456789abcdef" for c in sha256.lower().elems()]):
                fail("archive sha256 must be a 64-digit hexadecimal digest")
            ctx.download_and_extract(
                url = urls,
                sha256 = sha256,
                output = _local(ctx, step.get("output", ""), allow_empty = True),
                stripPrefix = _path(step.get("strip_prefix", ""), "archive strip_prefix", allow_empty = True),
            )
        elif op == "patch":
            _keys(step, ["op", "input"], ["strip", "format"])
            _patch(ctx, _input(ctx, step["input"]), _strip(step), _format(step))
        elif op == "patch_series":
            _series(ctx, step)
        elif op == "extract":
            _keys(step, ["op", "archive"], ["output", "strip_prefix"])
            ctx.extract(
                _local(ctx, step["archive"]),
                output = _local(ctx, step.get("output", ""), allow_empty = True),
                stripPrefix = _path(step.get("strip_prefix", ""), "extract strip_prefix", allow_empty = True),
            )
        else:
            fail("unknown source operation: " + str(op))
    ctx.file("BUILD.bazel", ctx.read(ctx.attr.build_file))

prepared_source = repository_rule(
    implementation = _prepared_source_impl,
    attrs = {
        "steps": attr.string_list(mandatory = True, doc = "Ordered JSON strings using archive, patch, patch_series or extract; json.encode is available in .bzl files."),
        "inputs": attr.string_keyed_label_dict(allow_files = True, doc = "Logical relative paths mapped to tracked patch/series file labels."),
        "build_file": attr.label(mandatory = True, allow_single_file = True),
    },
    doc = "Prepare source through fixed ordered archive/patch stages, then inject its BUILD file. Uses no shell commands.",
)
