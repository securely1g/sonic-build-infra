"""Validate a producer's final binary control file without rewriting its fields."""

import argparse
from pathlib import Path
import re


def check_control(text, package, version, architecture):
    if "${" in text:
        raise ValueError("control contains unresolved Debian substitution variables")
    fields = {}
    current = None
    ended = False
    for line in text.splitlines():
        if not line:
            ended = bool(fields)
            continue
        if ended:
            raise ValueError("expected one binary package stanza, not debian/control source stanzas")
        if line[0].isspace():
            if current is None:
                raise ValueError("control continuation has no field")
            fields[current] += "\n" + line
            continue
        key, separator, value = line.partition(":")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key):
            raise ValueError("invalid control field: " + line)
        current = key.lower()
        if current in fields:
            raise ValueError("duplicate control field: " + key)
        fields[current] = value.strip()
    for field, expected in (("package", package), ("version", version), ("architecture", architecture)):
        if fields.get(field) != expected:
            raise ValueError(f"control {field}: expected {expected!r}, found {fields.get(field)!r}")
    for field in ("maintainer", "description"):
        if not fields.get(field):
            raise ValueError("missing required control field: " + field)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "output", "package", "version", "architecture"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    content = Path(args.input).read_bytes()
    check_control(content.decode("utf-8"), args.package, args.version, args.architecture)
    Path(args.output).write_bytes(content)


if __name__ == "__main__":
    main()
