"""Check binary dependencies of a fixed package set without resolving or installing.

Debian's parser and version implementation supply relationship syntax and version
ordering. This module supports a single target architecture plus Architecture: all.
Pre-Depends is checked for availability and version, not installation ordering or
maintainer-script execution. Conflicts, Breaks and optional relationships are not
part of this check.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import re

from debian.deb822 import Deb822, PkgRelation
from debian.debian_support import NativeVersion


_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]+")
_ARCHITECTURE = re.compile(r"[a-z0-9][a-z0-9-]*")
_FIELD = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]*):.*")
_VERSION_TEXT = re.compile(r"[0-9A-Za-z.+:~-]+")
_REVISION = re.compile(r"[0-9A-Za-z+.~]+")
_OPERATORS = {"<<", "<=", "=", ">=", ">>"}


def _version(value, context):
    try:
        if not isinstance(value, str) or not _VERSION_TEXT.fullmatch(value):
            raise ValueError("invalid version characters")
        # NativeVersion is permissive about a trailing '-' and Unicode epochs.
        # Enforce Debian's syntax here, while keeping its comparison algorithm.
        if "-" in value and not _REVISION.fullmatch(value.rsplit("-", 1)[1]):
            raise ValueError("invalid or empty Debian revision")
        version = NativeVersion(value)
        if version.upstream_version[0] not in "0123456789":
            raise ValueError("upstream version must start with a digit")
        if version.epoch is None and ":" in version.upstream_version:
            raise ValueError("a colon requires an epoch")
        return version
    except ValueError as error:
        raise ValueError(f"{context}: invalid Debian version {value!r}") from error


@dataclass(frozen=True)
class Package:
    """Dependency metadata of the package whose files will actually be retained."""

    name: str
    version: str
    architecture: str
    depends: str = ""
    pre_depends: str = ""
    provides: str = ""
    multi_arch: str = "no"
    origin: str = ""

    def __post_init__(self):
        context = f"{self.name!r} ({self.origin})"
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError(f"{context}: invalid binary package name")
        _version(self.version, context)
        if not isinstance(self.architecture, str) or not _ARCHITECTURE.fullmatch(self.architecture):
            raise ValueError(f"{context}: invalid package architecture")
        if self.multi_arch not in ("no", "same", "foreign", "allowed"):
            raise ValueError(f"{context}: invalid Multi-Arch value {self.multi_arch!r}")
        if self.architecture == "all" and self.multi_arch == "same":
            raise ValueError(f"{context}: Architecture: all cannot be Multi-Arch: same")
        for field in ("depends", "pre_depends", "provides"):
            value = getattr(self, field)
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError(f"{context}: invalid {field} metadata")


def package_from_fields(fields: Mapping[str, str], *, origin: str) -> Package:
    """Read a complete control record, including caller-supplied retained metadata."""
    if not isinstance(fields, Mapping):
        raise ValueError(f"{origin}: expected a control-field mapping")
    normalized = {}
    for key, value in fields.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"{origin}: control fields must be strings")
        key = key.lower()
        if key in normalized:
            raise ValueError(f"{origin}: duplicate control field {key}")
        normalized[key] = value.strip()
    missing = {"package", "version", "architecture"} - normalized.keys()
    if missing:
        raise ValueError(f"{origin}: missing control fields: {', '.join(sorted(missing))}")
    return Package(
        normalized["package"], normalized["version"], normalized["architecture"],
        depends=normalized.get("depends", ""), pre_depends=normalized.get("pre-depends", ""),
        provides=normalized.get("provides", ""), multi_arch=normalized.get("multi-arch", "no"),
        origin=origin,
    )


def control_fields(package: Package) -> dict[str, str]:
    """Serialize the checked metadata for a later layer without source-specific origin."""
    return {
        "Package": package.name, "Version": package.version, "Architecture": package.architecture,
        "Depends": package.depends, "Pre-Depends": package.pre_depends,
        "Provides": package.provides, "Multi-Arch": package.multi_arch,
    }


def _paragraphs(data, origin):
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    if (not isinstance(text, str) or
            any((ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127 for char in text)):
        raise ValueError(f"{origin}: invalid control text")
    # Deb822 is deliberately tolerant. Reject lines or duplicate fields that it
    # would otherwise discard; leave field folding and paragraph parsing to it.
    seen = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            seen.clear()
        elif line[0] in " \t":
            if not seen:
                raise ValueError(f"{origin}:{number}: orphan control continuation")
        else:
            match = _FIELD.fullmatch(line)
            if not match:
                raise ValueError(f"{origin}:{number}: malformed control field")
            key = match[1].lower()
            if key in seen:
                raise ValueError(f"{origin}:{number}: duplicate control field {key}")
            seen.add(key)
    return list(Deb822.iter_paragraphs(text.splitlines(keepends=True), use_apt_pkg=False))


def package_from_control(data: bytes | str, *, origin: str) -> Package:
    """Parse exactly one binary control stanza, including folded relationships."""
    paragraphs = _paragraphs(data, origin)
    if len(paragraphs) != 1:
        raise ValueError(f"{origin}: expected exactly one binary control stanza")
    return package_from_fields(paragraphs[0], origin=origin)


def installed_packages(status: bytes | str, *, origin: str) -> dict[str, Package]:
    """Read dependency metadata only for fully installed packages in dpkg status."""
    result = {}
    for fields in _paragraphs(status, origin):
        # The desired action (install/hold/deinstall/...) is separate from the
        # current state. A held or removal-selected package still supplies files.
        status_fields = fields.get("Status", "").split()
        if len(status_fields) != 3 or status_fields[1:] != ["ok", "installed"]:
            continue
        package = package_from_fields(fields, origin=origin)
        if package.name in result:
            raise ValueError(f"{origin}: duplicate installed package {package.name}")
        result[package.name] = package
    return result


def _relations(raw, package, field, architecture):
    if not raw.strip():
        return []
    context = f"{package.name} ({package.origin}) {field}: {raw!r}"
    try:
        relations = PkgRelation.parse_relations(raw)
    except (ValueError, IndexError) as error:
        raise ValueError(f"{context}: malformed package relationship") from error
    for alternatives in relations:
        if field == "Provides" and len(alternatives) != 1:
            raise ValueError(f"{context}: Provides cannot contain alternatives")
        for relation in alternatives:
            # PkgRelation returns malformed input as an unparsed name instead
            # of raising. Validate every alternative, even if another matches.
            if not _NAME.fullmatch(relation["name"]):
                raise ValueError(f"{context}: malformed package relationship")
            if relation["arch"] or relation["restrictions"]:
                raise ValueError(f"{context}: source architecture/profile restrictions are unsupported")
            qualifier = relation["archqual"]
            if qualifier == "native":
                raise ValueError(f"{context}: :native is a source-build qualifier, unsupported in binary metadata")
            supported = (None, architecture) if field == "Provides" else (None, architecture, "any")
            if qualifier not in supported:
                raise ValueError(f"{context}: unsupported architecture qualifier {qualifier!r}")
            if relation["version"]:
                operator, version = relation["version"]
                if operator not in ({"="} if field == "Provides" else _OPERATORS):
                    raise ValueError(f"{context}: invalid version operator {operator!r}")
                _version(version, context)
    return relations


def _satisfies(relation, provider, version):
    if relation["archqual"] == "any" and provider.multi_arch != "allowed":
        return False
    if relation["version"] is None:
        return True
    if version is None:
        return False  # An unversioned Provides cannot satisfy a versioned dependency.
    operator, expected = relation["version"]
    actual = _version(version, provider.name)
    expected = _version(expected, provider.name)
    return {"<<": actual < expected, "<=": actual <= expected, "=": actual == expected,
            ">=": actual >= expected, ">>": actual > expected}[operator]


def validate(packages: Mapping[str, Package], *, architecture: str) -> dict:
    """Reject unsatisfied Depends/Pre-Depends without changing the chosen set.

    All final packages are checked, including base and Make-retained records.
    No installation order, configuration state of added payloads, Conflicts,
    Breaks, or optional dependencies are inferred from a successful result.
    """
    if (not isinstance(architecture, str) or not _ARCHITECTURE.fullmatch(architecture) or
            architecture in ("all", "any", "native", "source")):
        raise ValueError(f"invalid target architecture {architecture!r}")
    providers = defaultdict(list)
    dependencies = {}
    for name, package in sorted(packages.items()):
        if not isinstance(package, Package) or name != package.name:
            raise ValueError(f"invalid final package record for {name!r}")
        if package.architecture not in (architecture, "all"):
            raise ValueError(f"{name} ({package.origin}): foreign package architecture {package.architecture!r}")
        providers[name].append((package, package.version))
        for alternatives in _relations(package.provides, package, "Provides", architecture):
            provided = alternatives[0]
            version = provided["version"][1] if provided["version"] else None
            providers[provided["name"]].append((package, version))
        dependencies[name] = {
            "Depends": _relations(package.depends, package, "Depends", architecture),
            "Pre-Depends": _relations(package.pre_depends, package, "Pre-Depends", architecture),
        }
    counts = {"Depends": 0, "Pre-Depends": 0}
    for name, fields in dependencies.items():
        for field, groups in fields.items():
            for alternatives in groups:
                counts[field] += 1
                if not any(_satisfies(relation, provider, version)
                           for relation in alternatives
                           for provider, version in providers[relation["name"]]):
                    expression = PkgRelation.str([alternatives])
                    raise ValueError(f"{name} ({packages[name].origin}): unsatisfied {field}: {expression}")
    return {
        "schema": 1, "status": "satisfied", "package_count": len(packages),
        "depends_groups": counts["Depends"], "pre_depends_groups": counts["Pre-Depends"],
        "scope": "package presence and version constraints; installation ordering is not checked",
    }
