"""Check the indirect dependency and RUNPATH used by the native DSO test."""

import re
import subprocess
import sys


readelf, executable, middle, leaf = sys.argv[1:]


def dynamic_tags(path):
    dynamic = subprocess.check_output([readelf, "--dynamic", path], text=True)
    print(path, dynamic, sep="\n")
    tags = {}
    for tag, value in re.findall(r"\((\w+)\).*?\[([^\]]*)\]", dynamic):
        tags.setdefault(tag, []).append(value)
    return tags


executable_tags = dynamic_tags(executable)
middle_tags = dynamic_tags(middle)
leaf_tags = dynamic_tags(leaf)

# The executable exercises an indirect dependency rather than linking the leaf.
assert "libnative_middle.so.0" in executable_tags.get("NEEDED", [])
assert "libnative_leaf.so.0" not in executable_tags.get("NEEDED", [])
assert "libnative_leaf.so.0" in middle_tags.get("NEEDED", [])
assert middle_tags.get("SONAME") == ["libnative_middle.so.0"]
assert leaf_tags.get("SONAME") == ["libnative_leaf.so.0"]

# The fixtures use the toolchain's default, without --enable-new-dtags overrides.
# An executable-wide RPATH would hide errors in the producer's own paths or
# transitive runfiles. Detect a change to Debian's default RUNPATH behavior.
assert "RUNPATH" in executable_tags, "the executable must use default RUNPATH"
assert "RUNPATH" in middle_tags, "the middle library must use default RUNPATH"
for name, tags in (
    ("executable", executable_tags),
    ("middle library", middle_tags),
    ("leaf library", leaf_tags),
):
    assert "RPATH" not in tags, f"{name} must not use inherited RPATH"
