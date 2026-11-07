"""Reuse the pinned APT implementation with a separate Bookworm index namespace.

Retained for consumers that explicitly use the historical extension namespace.
This repository now imports Bookworm from its shared reviewed production lock;
apt/lock.py isolates distribution indexes only during an explicit update.
The retained namespace imports that same lock for registered libnl/libyang
owners, including their target libraries and native header file maps.

The upstream resolver indexes packages by architecture/name/version, so equal
versions in two suites overwrite their source identity. Separate extension
usage keeps Trixie and Bookworm package/control metadata independent.
"""

load("@rules_distroless//apt:extensions.bzl", _apt = "apt")

apt = _apt
