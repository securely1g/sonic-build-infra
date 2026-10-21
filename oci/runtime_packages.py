"""Generate runtime package metadata using Debian's control and ELF tools.

The inputs are source-owned Debian declarations and actual runtime TARs.  The
output directory is a private staging area for downstream dependency analysis;
it is never an image layer or an installable Debian package.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile

from debian.changelog import Changelog
from debian.deb822 import Deb822


ARCHITECTURES = {"amd64": 62, "arm64": 183, "armhf": 40}
RELATION_FIELDS = {
    "Depends", "Pre-Depends", "Recommends", "Suggests", "Enhances",
    "Breaks", "Conflicts", "Provides", "Replaces",
}
INSTALLED_FIELDS = {
    "Architecture", "Status", "Installed-Size", "Conffiles", "MD5sum",
    "SHA1", "SHA256", "Size", "Filename",
}
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]*")
VARIABLE = re.compile(r"\$\{([^{}]+)\}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def paragraphs(path):
    return [dict(item) for item in Deb822.iter_paragraphs(Path(path).read_text().splitlines())]


def source_definition(control, changelog=None):
    """Distinguish source templates from existing finalized binary controls."""
    entries = paragraphs(control)
    require(entries, "empty Debian control input")
    if "Source" in entries[0] and "Package" not in entries[0]:
        require(changelog is not None, "source control requires debian/changelog")
        with Path(changelog).open() as stream:
            # Some maintained SONiC changelogs have incomplete historical
            # trailer dates. dpkg accepts their package/version with a warning.
            log = Changelog(stream, strict=False)
        require(log.package == entries[0]["Source"], "control and changelog source names differ")
        version = str(log.version)
        source, binaries = entries[0], entries[1:]
    else:
        require(len(entries) == 1 and changelog is None,
                "finalized control requires one package and its existing Version")
        require("Package" in entries[0] and entries[0].get("Version"),
                "control lacks its source stanza or finalized package Version")
        require(not any(VARIABLE.search(value) for value in entries[0].values()),
                "finalized control cannot contain unresolved substitutions")
        binary = entries[0]
        version = binary["Version"]
        source = {key: binary[key] for key in ("Maintainer", "Section", "Priority", "Homepage") if key in binary}
        source["Source"] = binary.get("Source", binary["Package"]).split()[0]
        binaries = [{key: value for key, value in binary.items()
                     if key not in INSTALLED_FIELDS | {"Version", "Source"}}]
        # A finalized binary control records one past build's architecture.
        # This action describes the selected Bazel target, whose ELF headers
        # are checked below; retain genuinely architecture-independent output.
        binaries[0]["Architecture"] = "all" if binary.get("Architecture") == "all" else "any"
    require(binaries and all(PACKAGE.fullmatch(item.get("Package", "")) for item in binaries),
            "invalid binary package declarations")
    require(len({item["Package"] for item in binaries}) == len(binaries), "duplicate Debian package stanza")
    return source, {item["Package"]: item for item in binaries}, version


def read_substvars(path):
    """Read Debian substitution values without discarding unknown variables."""
    result = {}
    if path is None:
        return result
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9:-]*)([?!]?=)(.*)", line)
        require(match is not None, "invalid Debian substitution variable")
        name, _, value = match.groups()
        require(name not in result, "duplicate Debian substitution variable: " + name)
        result[name] = value
    return result


def check_substitutions(fields, variables):
    # These built-ins are supplied by dpkg-gencontrol from the declared source,
    # changelog and target architecture, not from the execution host.
    builtins = {"Arch", "source:Version", "source:Upstream-Version", "binary:Version",
                "source:Synopsis", "source:Extended-Description", "Newline", "Space", "Tab",
                "Installed-Size", "Extra-Size", "vendor:Name", "vendor:Id",
                "dpkg:Version", "dpkg:Upstream-Version"}
    pending = set(VARIABLE.findall("\n".join(fields.values())))
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        require(name in variables or name in builtins or name.startswith(("S:", "F:")),
                "unresolved Debian substitution variable: " + name)
        if name in variables:
            pending.update(VARIABLE.findall(variables[name]))


def validate_archive(path):
    """Inspect names before handing extraction to the declared BSD tar tool."""
    with tarfile.open(path) as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts, "unsafe runtime TAR path")
            require(member.isdir() or member.isfile() or member.issym() or member.islnk(),
                    "unsupported runtime TAR entry")
            if member.islnk():
                target = PurePosixPath(member.linkname)
                require(not target.is_absolute() and ".." not in target.parts, "unsafe runtime TAR hardlink")


def extract(path, destination, tar):
    validate_archive(path)
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(tar), "--no-same-owner", "--no-same-permissions", "-xf", str(path),
                    "-C", str(destination)], check=True, capture_output=True)
    # Installed absolute links refer to this package tree, never the build host.
    # Dangling links to another declared package remain dangling here; library
    # search considers that package's tree independently.
    for directory, dirs, files in os.walk(destination, followlinks=False):
        for name in dirs + files:
            link = Path(directory) / name
            if link.is_symlink():
                target = link.readlink()
                if target.is_absolute():
                    target = Path(os.path.relpath(destination / str(target).lstrip("/"), link.parent))
                    link.unlink()
                    link.symlink_to(target)
                resolved = Path(os.path.abspath(link.parent / target))
                require(resolved.is_relative_to(destination), "runtime TAR symlink escapes package")


def elf_files(root, architecture):
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or "DEBIAN" in path.relative_to(root).parts:
            continue
        with path.open("rb") as stream:
            header = stream.read(20)
        if not header.startswith(b"\x7fELF"):
            continue
        require(len(header) == 20 and header[5] in (1, 2), "invalid runtime ELF header")
        endian = "<" if header[5] == 1 else ">"
        kind, machine = struct.unpack_from(endian + "HH", header, 16)
        require(machine == ARCHITECTURES[architecture], "runtime ELF differs from selected target architecture")
        if kind in (2, 3):
            files.append(path)
    return files


def finish_analysis_tree(root):
    """Keep a cacheable analysis copy without changing the input runtime TAR.

    Debian documentation links commonly point into a different package (for
    example libgcc-s1 -> gcc-14-base). They are irrelevant to ELF dependency
    analysis and cannot remain dangling in a Bazel tree artifact. Directory
    aliases can also create traversal loops. Record these omitted aliases;
    actual file bytes and the original TAR hash remain authoritative.
    """
    omitted = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if not path.is_symlink():
                continue
            try:
                omit = not path.exists() or path.is_dir()
            except (OSError, RuntimeError):
                omit = True
            if omit:
                omitted[str(path.relative_to(root))] = str(path.readlink())
                path.unlink()
    return omitted


def tool_environment(tools, scratch, architecture):
    database = scratch / "dpkg"
    (database / "info").mkdir(parents=True)
    (database / "status").write_text("")
    (database / "info/format").write_text("1\n")
    target_root = scratch / "empty-target-root"
    target_root.mkdir()
    environment = {
        "PATH": str(tools / "bin"), "HOME": str(scratch), "TMPDIR": str(scratch),
        "LANG": "C", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "DPKG_NLS": "0",
        "DEB_HOST_ARCH": architecture, "DPKG_ADMINDIR": str(database),
        "SONIC_DPKG_SHLIBS_ROOT": str(target_root),
        # dh_gencontrol supplies empty misc variables when no helper generated
        # a dependency. Actual helper-produced values may be passed explicitly.
        "DEB_RULES_REQUIRES_ROOT": "no",
    }
    return environment


class DebianTools:
    def __init__(self, root, scratch, architecture):
        self.root = root
        self.scratch = scratch
        self.environment = tool_environment(root, scratch, architecture)

    def run(self, tool, *arguments, cwd=None):
        result = subprocess.run([str(self.root / "bin" / tool), *map(str, arguments)],
                                cwd=cwd or self.scratch, env=self.environment,
                                text=True, capture_output=True)
        if result.returncode or re.search(r"substitution variable .* (?:not defined|not used)", result.stderr):
            raise ValueError(tool + " failed:\n" + result.stdout + result.stderr)
        return result.stdout


def package_record(root, kind, digest):
    fields, = paragraphs(root / "DEBIAN/control")
    name = fields.get("Package", "")
    require(PACKAGE.fullmatch(name) and fields.get("Version"), "invalid dependency package controls")
    return {"name": name, "root": root, "fields": fields, "kind": kind, "payload_sha256": digest}


def dependency_records(directories, architecture):
    records = []
    for directory in directories:
        manifest = json.loads((directory / "manifest.json").read_text())
        require(manifest.get("schema") == 1 and manifest.get("architecture") == architecture,
                "dependency metadata differs from target architecture")
        for item in manifest["packages"]:
            relative = PurePosixPath(item["root"])
            require(not relative.is_absolute() and ".." not in relative.parts,
                    "unsafe dependency staging path")
            root = directory / relative
            require(sha(root / "DEBIAN/control") == item["control_sha256"],
                    "dependency control changed after generation")
            records.append(package_record(root, item["kind"], item["payload_sha256"]))
    return records


def unique_dependencies(records, architecture):
    selected = {}
    for record in records:
        require(record["fields"].get("Architecture") in (architecture, "all"),
                "dependency control differs from target architecture")
        name = record["name"]
        previous = selected.get(name)
        if previous:
            if previous["kind"] != record["kind"]:
                if record["kind"] == "source":
                    selected[name] = record
                continue
            require(previous["fields"] == record["fields"] and
                    previous["payload_sha256"] == record["payload_sha256"],
                    "conflicting declared dependency package: " + name)
        else:
            selected[name] = record
    return selected


def library_directories(roots):
    directories = set()
    for root in roots:
        for path in root.rglob("*"):
            if (".so" in path.name and path.is_file()) or path.is_symlink() and ".so" in path.name:
                directories.add(path.parent)
    return sorted(directories)


def helper_substitutions(dpkg, name, root, fields, debian, provided):
    """Run the standard producers of non-ELF Debian substitutions.

    Helpers may rewrite shebangs or generate maintainer-script fragments. Work
    on a private copy, retain only the substitution values, and never execute
    generated scripts or change the runtime payload being described.
    """
    needed = set(VARIABLE.findall("\n".join(fields.values())))
    commands = []
    if any(value.startswith("t64:") for value in needed - provided.keys()):
        commands.append(("dh_makeshlibs", ["--no-scripts"]))
    if any(value.startswith("python3:") for value in needed - provided.keys()):
        commands.append(("dh_python3", []))
    if any(value.startswith("perl:") for value in needed - provided.keys()):
        commands.append(("dh_perl", []))
    if (debian / (name + ".templates")).exists() or (debian / "templates").exists():
        commands.append(("dh_installdebconf", []))
    # Installed service files carry helper dependencies too. --only-scripts
    # reads the already-staged init name without reinstalling it under the
    # binary package name (for example syncd-vs installs /etc/init.d/syncd).
    for script in sorted((root / "etc/init.d").glob("*")):
        if script.is_file() and script.stat().st_mode & 0o111:
            commands.append(("dh_installinit", ["--only-scripts", "--name=" + script.name]))
    for relative, tool in (("usr/lib/systemd/system", "dh_installsystemd"),
                           ("lib/systemd/system", "dh_installsystemd"),
                           ("usr/lib/systemd/user", "dh_installsystemduser"),
                           ("lib/systemd/user", "dh_installsystemduser")):
        if (root / relative).is_dir() and any((root / relative).iterdir()):
            if (tool, []) not in commands:
                commands.append((tool, []))
    if not commands:
        return {}
    # dh_python3 uses debian/<package> and does not implement debhelper's -P.
    # Use the standard staging path for every helper for consistent behavior.
    helper_root = debian / name
    shutil.copytree(root, helper_root, symlinks=True)
    variable_file = debian / (name + ".substvars")
    # The helper owns these values; stale externally supplied files must not
    # mask an absent producer on a subsequent invocation.
    variable_file.unlink(missing_ok=True)
    for tool, options in commands:
        dpkg.run(tool, "-p" + name, *options)
    return read_substvars(variable_file) if variable_file.exists() else {}


def generate(*, control, changelog, module, architecture, tools, tar, output, tree,
             packages, dependencies=(), apt_packages=(), symbols=None, substvars=None, debian_files=None):
    source, stanzas, version = source_definition(control, changelog)
    require(module and set(packages) <= set(stanzas), "runtime package mapping differs from Debian control")
    require(packages, "runtime package mapping is empty")
    symbols, substvars = symbols or {}, substvars or {}
    require(set(symbols) <= set(packages) and set(substvars) <= set(packages),
            "symbols or substitutions refer to an unmapped package")
    tree.mkdir(parents=True, exist_ok=True)
    require(not any(tree.iterdir()), "runtime metadata output tree is not empty")
    with tempfile.TemporaryDirectory(prefix="runtime-package-metadata-") as temporary:
        scratch = Path(temporary)
        debian = scratch / "debian"
        debian.mkdir()
        for relative, path in (debian_files or {}).items():
            relative = PurePosixPath(relative)
            require(not relative.is_absolute() and ".." not in relative.parts and
                    str(relative) not in ("control", "changelog"), "invalid additional Debian metadata path")
            destination = debian / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        # Preserve Debian field placement, including shlibs in Recommends.
        generated_stanzas = {name: dict(fields) for name, fields in stanzas.items()}
        for name in packages:
            fields = generated_stanzas[name]
            if not any("${shlibs:" in value for value in fields.values()):
                fields["Depends"] = ", ".join(filter(None, [fields.get("Depends"), "${shlibs:Depends}"]))
        (debian / "control").write_text("\n".join(Deb822(item).dump() for item in [source, *generated_stanzas.values()]))
        if changelog:
            shutil.copyfile(changelog, debian / "changelog")
        else:
            (debian / "changelog").write_text(
                source["Source"] + " (" + version + ") unstable; urgency=medium\n\n"
                "  * Metadata generated from the existing binary control.\n\n"
                " -- " + source["Maintainer"] + "  Thu, 01 Jan 1970 00:00:00 +0000\n")
        dpkg = DebianTools(tools, scratch, architecture)
        external = dependency_records(dependencies, architecture)
        apt_records = []
        apt_inputs = set()
        for index, (data, control_tar) in enumerate(apt_packages):
            key = (sha(data), sha(control_tar))
            if key in apt_inputs:
                continue
            apt_inputs.add(key)
            root = tree / "apt" / str(index)
            extract(data, root, tar)
            extract(control_tar, root / "DEBIAN", tar)
            record = package_record(root, "apt", sha(data))
            apt_records.append(record)
        selected = unique_dependencies([*external, *apt_records], architecture)
        own = {}
        for name, (label, payload) in packages.items():
            require(label.startswith("//") and ":" in label, "runtime target must be a public owner-relative label")
            root = tree / "packages" / name
            extract(payload, root, tar)
            require(not (root / "DEBIAN").exists(), "runtime TAR unexpectedly contains Debian control files")
            (root / "DEBIAN").mkdir()
            native = elf_files(root, architecture)
            # Generate actual source library symbol metadata before calculating
            # any package's dependencies; sibling packages may depend on it.
            args = ["-p" + name, "-v" + version, "-a" + architecture, "-P" + str(root),
                    "-O" + str(root / "DEBIAN/symbols")]
            if name in symbols:
                args.append("-I" + str(symbols[name]))
            if native:
                dpkg.run("dpkg-gensymbols", *args, *("-e" + str(path) for path in native))
            fields = dict(generated_stanzas[name], Version=version,
                          Architecture="all" if generated_stanzas[name]["Architecture"] == "all" else architecture)
            # The early control gives dpkg-shlibdeps package identity. Its final
            # dependency fields are produced below by dpkg-gencontrol.
            (root / "DEBIAN/control").write_text(Deb822(fields).dump())
            own[name] = {"name": name, "root": root, "fields": fields, "kind": "source",
                         "payload_sha256": sha(payload), "label": label, "native": native}
        selected.update(own)
        roots = [item["root"] for _, item in sorted(selected.items())]
        # A Bazel input tree may materialize each file as a symlink into the
        # cached output. Authorize those declared files' real parent paths as
        # well as the package roots; do not authorize unrelated RPATH entries.
        allowed_roots = set(roots)
        for root in roots:
            for path in root.rglob("*"):
                if path.is_file():
                    allowed_roots.add(path.resolve().parent)
        dpkg.environment["SONIC_DPKG_TARGET_ROOTS"] = "\n".join(map(str, sorted(allowed_roots)))
        search = ["-S" + str(root) for root in roots]
        search += ["-l" + str(directory) for directory in library_directories(roots)]
        result = {}
        for name, record in own.items():
            supplied = read_substvars(substvars.get(name))
            variables = dict(supplied)
            generated = helper_substitutions(dpkg, name, record["root"], generated_stanzas[name], debian, variables)
            require(not set(generated) & set(variables), "duplicate generated and supplied Debian substitutions")
            variables.update(generated)
            # Match dh_gencontrol's defaults. These values describe the absence
            # of helper-generated installation dependencies in TAR-only output;
            # callers with such dependencies supply the actual helper output.
            variables.setdefault("misc:Depends", "")
            variables.setdefault("misc:Pre-Depends", "")
            variables.setdefault("misc:Built-Using", "")
            require(not any(key.startswith("shlibs:") for key in variables),
                    "shlibs substitutions must be computed from the runtime payload")
            if record["native"]:
                data = dpkg.run("dpkg-shlibdeps", "-O", "-x" + name,
                                "--admindir=" + dpkg.environment["DPKG_ADMINDIR"],
                                *search, *("-e" + str(path) for path in record["native"]))
                for line in data.splitlines():
                    key, value = line.split("=", 1)
                    require(key.startswith("shlibs:"), "unexpected dpkg-shlibdeps output")
                    variables[key] = value
            variables.setdefault("shlibs:Depends", "")
            check_substitutions(generated_stanzas[name], variables)
            variable_file = debian / (name + ".substvars")
            variable_file.write_text("".join(key + "?=" + value + "\n" for key, value in sorted(variables.items())
                                            if key not in supplied))
            control_output = record["root"] / "DEBIAN/control"
            # Preserve Debian's ?=/!= semantics in explicitly supplied
            # substvars instead of rewriting required values as optional.
            provided_args = ["-T" + str(substvars[name])] if name in substvars else []
            dpkg.run("dpkg-gencontrol", "-p" + name, "-P" + str(record["root"]),
                     *provided_args, "-T" + str(variable_file), "-O" + str(control_output), "-f" + str(debian / "files"))
            fields, = paragraphs(control_output)
            require(fields.get("Package") == name and fields.get("Version") == version and
                    fields.get("Architecture") in (architecture, "all"), "generated package identity differs")
            require(not any(VARIABLE.search(value) for value in fields.values()),
                    "generated package control contains unresolved substitutions")
            record["fields"] = fields
            result[name] = {"runtime_target": record["label"],
                            "control_fields": {key: value for key, value in fields.items() if key not in INSTALLED_FIELDS},
                            "payload_sha256": record["payload_sha256"]}
        # Dependencies are supplied transitively by the Starlark provider. Only
        # this action's newly staged source/APT trees belong in its manifest.
        manifest = {"schema": 1, "architecture": architecture, "packages": [
            {"root": str(item["root"].relative_to(tree)), "kind": item["kind"],
             "payload_sha256": item["payload_sha256"], "control_sha256": sha(item["root"] / "DEBIAN/control"),
             "omitted_analysis_links": finish_analysis_tree(item["root"])}
            for item in [*apt_records, *own.values()]
        ]}
        (tree / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        output.write_text(json.dumps({"schema": 1, "module": module, "architecture": architecture,
            "_generated": {"generator": "sonic-build-infra/oci/runtime_packages.py",
                           "control_sha256": sha(control), "changelog_sha256": sha(changelog) if changelog else None},
            "packages": result}, sort_keys=True, indent=2) + "\n")


def merge(documents, output):
    result = None
    for path in documents:
        document = json.loads(path.read_text())
        require(document.get("schema") == 1 and document.get("module") and document.get("architecture"),
                "invalid generated package metadata")
        if result is None:
            result = {key: document[key] for key in ("schema", "module", "architecture")}
            result.update(packages={}, _generated={"generator": "sonic-build-infra/oci/runtime_packages.py",
                                                 "inputs": []})
        require(all(document[key] == result[key] for key in ("module", "architecture")),
                "cannot combine different owner modules or target architectures")
        require(not set(result["packages"]) & set(document["packages"]), "duplicate generated package metadata")
        result["packages"].update(document["packages"])
        result["_generated"]["inputs"].append(sha(path))
    require(result is not None, "package metadata group is empty")
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")


def named_paths(values):
    result = {}
    for value in values:
        name, path = value.split("=", 1)
        require(name not in result, "duplicate named input: " + name)
        result[name] = Path(path).resolve()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "changelog", "tools", "tar", "output", "tree"):
        parser.add_argument("--" + name, type=Path, required=name == "output")
    parser.add_argument("--module")
    parser.add_argument("--architecture", choices=ARCHITECTURES)
    for name in ("package", "dependency", "apt-package", "symbols", "substvars", "merge", "debian-file"):
        parser.add_argument("--" + name, action="append", default=[])
    args = parser.parse_args()
    if args.merge:
        merge([Path(path) for path in args.merge], args.output)
        return
    require(all(getattr(args, name) is not None for name in ("control", "tools", "tar", "tree", "module", "architecture")),
            "runtime metadata generation requires control, tools, tar, tree, module and architecture")
    packages = {}
    for entry in args.package:
        name, label, path = entry.split("=", 2)
        require(PACKAGE.fullmatch(name) and name not in packages, "invalid or duplicate runtime package mapping")
        packages[name] = label, Path(path).resolve()
    generate(control=args.control.resolve(), changelog=args.changelog.resolve() if args.changelog else None,
             tools=args.tools.resolve(), tar=args.tar.resolve(), output=args.output.resolve(), tree=args.tree.resolve(),
             module=args.module, architecture=args.architecture, packages=packages,
             dependencies=[Path(path).resolve() for path in args.dependency],
             apt_packages=[tuple(Path(path).resolve() for path in value.split("=", 1)) for value in args.apt_package],
             symbols=named_paths(args.symbols), substvars=named_paths(args.substvars),
             debian_files=named_paths(args.debian_file))


if __name__ == "__main__":
    main()
