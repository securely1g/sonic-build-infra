"""Reuse the pinned APT implementation with a separate Bookworm index namespace.

The upstream resolver indexes packages by architecture/name/version, so equal
versions in two suites overwrite their source identity. Separate extension
usage keeps Trixie and Bookworm package/control metadata independent.
"""

load("@rules_distroless//apt:extensions.bzl", _apt = "apt")

apt = _apt
