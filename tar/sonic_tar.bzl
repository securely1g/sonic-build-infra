"""Tar archives with fixed default timestamps for explicit entry lists."""

load("@tar.bzl//tar:tar.bzl", "tar")

# Match tar.bzl's automatically generated file/directory entries. Explicit links
# historically defaulted to zero; retaining that value avoids changing their bytes.
DEFAULT_FILE_TIME = "1672560000"

def timestamped_mtree(entries):
    """Fill missing times in flat mtree entries, preserving explicit metadata.

    Paths with whitespace must use mtree escapes, such as \\040. Comments and
    blank lines are retained. Stateful /set, /unset and .. lines and continued
    lines need a generated, normalized manifest and upstream tar instead.
    """
    result = []
    for line in entries:
        if "\n" in line or "\r" in line or line.rstrip().endswith("\\"):
            fail("sonic_tar requires one complete mtree entry per line; use upstream tar for continued manifests")
        fields = [field for field in line.replace("\t", " ").split(" ") if field]
        if not fields or fields[0].startswith("#"):
            result.append(line)
            continue
        if fields[0] in ["/set", "/unset", ".."]:
            fail("sonic_tar does not support /set, /unset or ..; use upstream tar with a normalized manifest")
        attributes = fields[1:]
        if any([field.startswith("time=") for field in attributes]):
            result.append(line)
        else:
            timestamp = "0" if "type=link" in attributes else DEFAULT_FILE_TIME
            result.append(line + " time=" + timestamp)
    return result

def sonic_tar(name, mtree = "auto", **kwargs):
    """Use tar.bzl with deterministic defaults for auto or flat-list manifests.

    Explicit times, paths, contents, ownership and modes are left unchanged.
    Label-valued manifests should use upstream tar after their producer sets
    deterministic times. All other arguments are passed to tar.bzl unchanged.
    """
    if type(mtree) == "list":
        mtree = timestamped_mtree(mtree)
    elif mtree != "auto":
        fail("sonic_tar accepts 'auto' or a flat mtree list; use upstream tar for a normalized manifest label")
    tar(name = name, mtree = mtree, **kwargs)
