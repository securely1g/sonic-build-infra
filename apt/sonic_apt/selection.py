"""Verify locked APT payloads and retain packages supplied by the image base."""

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
import tarfile

from . import dependencies
from .inputs import package_keys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def base_packages(layers, *, architecture):
    target = "var/lib/dpkg/status"
    status = None
    for layer in layers:
        removed = False
        added = None
        with tarfile.open(layer, "r:*") as archive:
            for member in archive:
                pure = PurePosixPath(member.name)
                require(not pure.is_absolute() and ".." not in pure.parts, "unsafe archive path: " + member.name)
                name = str(pure)
                if name == target:
                    require(member.isfile(), "base dpkg status is not a regular file")
                    added = archive.extractfile(member).read()
                elif pure.name == ".wh..wh..opq":
                    parent = str(pure.parent)
                    removed |= parent == "." or target.startswith(parent + "/")
                elif pure.name.startswith(".wh."):
                    hidden = str(pure.parent / pure.name[4:])
                    removed |= target == hidden or target.startswith(hidden + "/")
        if removed:
            status = None
        if added is not None:
            status = added
    require(status is not None, "OCI base lacks dpkg status")
    result = dependencies.installed_packages(status, origin="OCI base")
    require(result, "OCI base has no installed packages")
    require(all(package.architecture in (architecture, "all") for package in result.values()),
            "invalid or foreign installed package in OCI base")
    return result


def package_control(path, *, identity):
    """Read one regular control file and bind its identity to the reviewed lock."""
    with tarfile.open(path, "r:*") as archive:
        controls = []
        for member in archive:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts,
                    "unsafe control archive path: " + member.name)
            if str(name) == "control":
                controls.append(member)
        require(len(controls) == 1 and controls[0].isfile(),
                "expected one regular control file: " + str(path))
        record = dependencies.package_from_control(
            archive.extractfile(controls[0]).read(), origin="APT " + identity["name"])
    require((record.name, record.version, record.architecture) ==
            (identity["name"], identity["version"], identity["architecture"]),
            "APT control identity differs from reviewed lock: " + identity["name"])
    return record


def select(lock_path, mapping_path, *, group, architecture, installed, base_files,
           retained_packages, inspect_payload, check_overlay, base_package_metadata=None,
           retained_replacements=None):
    """Select verified payloads using the consumer's existing file inventory reader.

    Inventories map relative paths to kind/mode/uid/gid/hash/link/ELF metadata.
    inspect_payload(path) returns that map and rejects unsafe archive entries;
    check_overlay(selected_files, base_files) rejects paths crossing symlinks.
    installed maps names to full dependency Package records from base_packages.
    Retained packages map names to source_sha256 and full Debian control fields
    under "control"; names and hashes alone cannot establish compatibility.
    base_package_metadata optionally carries the validated package inventory of
    an inherited APT layer whose additions are absent from the dpkg database.
    retained_replacements maps explicitly replaced inherited package names to
    their exact previous control fields. The consumer must verify the replacement
    payload and its policy before requesting this inventory transition. Installed
    dpkg packages cannot be replaced through this option.
    """
    lock = json.loads(lock_path.read_bytes())
    mapping = json.loads(mapping_path.read_bytes())
    require(mapping.get("architecture") == architecture,
            "APT package set has a foreign target architecture")
    entries = mapping["locked"]
    locked = {item["key"]: lock["packages"][item["key"]] for item in entries}
    require(len(locked) == len(entries), "duplicate package in APT package set")
    expected = package_keys(lock, mapping.get("dependency_set", group), architecture)
    require(set(locked) == set(expected), "APT inputs do not match the reviewed dependency set")
    paths = {item["key"]: {kind: Path(item[kind]) for kind in ("payload", "control")} for item in entries}
    # Ordinary public Distroless targets supply data/control files; the lock
    # supplies identities. Verify the complete candidate set before selection.
    # Preserve optional reviewed extracted hashes used by SONiC's Make handoff.
    content, metadata = {}, {}
    for key, package in sorted(locked.items()):
        require(package["architecture"] in (architecture, "all"),
                "APT package has a foreign architecture: " + key)
        content[key] = {}
        for kind in ("payload", "control"):
            path = paths[key][kind]
            size, digest = path.stat().st_size, sha(path)
            if kind + "_sha256" in package or kind + "_size" in package:
                require(size == package.get(kind + "_size") and digest == package.get(kind + "_sha256"),
                        "changed locked APT " + kind + ": " + key)
            content[key][kind + "_sha256"] = digest
            content[key][kind + "_size"] = size
        metadata[key] = package_control(paths[key]["control"], identity=package)
    require(all(isinstance(record, dependencies.Package) for record in installed.values()),
            "base package inventory lacks Debian dependency metadata")
    installed_fields = {name: dependencies.control_fields(record) for name, record in sorted(installed.items())}
    installed_digest = hashlib.sha256(json.dumps(installed_fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    final_packages = dict(installed)
    if base_package_metadata is not None:
        document = json.loads(base_package_metadata.read_bytes())
        inherited = document.get("dependency_check") if isinstance(document, dict) else None
        require(isinstance(inherited, dict), "base package metadata lacks a dependency inventory")
        require(inherited.get("schema") == 1 and inherited.get("status") == "satisfied",
                "base package metadata lacks a successful dependency check")
        require(inherited.get("base_package_inventory_sha256") == installed_digest,
                "base package metadata does not match the inherited dpkg inventory")
        fields = inherited.get("packages")
        require(isinstance(fields, dict) and installed.keys() <= fields.keys(),
                "base package metadata omits installed packages")
        require(all(isinstance(control, dict) for control in fields.values()),
                "base package metadata has an invalid control record")
        final_packages = {name: dependencies.package_from_fields(control, origin="inherited APT inventory")
                          for name, control in fields.items()}
        require(all(name == record.name for name, record in final_packages.items()),
                "base package metadata name differs from control record")
        require(all(dependencies.control_fields(final_packages[name]) == control
                    for name, control in installed_fields.items()),
                "base package metadata changes an installed package control record")
    replacements = {} if retained_replacements is None else retained_replacements
    require(isinstance(replacements, dict), "retained replacements must be a control mapping")
    for name, expected_control in replacements.items():
        require(name in retained_packages, "replacement is not a retained package: " + str(name))
        require(name not in installed, "replacement cannot change an installed package: " + name)
        require(base_package_metadata is not None and name in final_packages,
                "replacement lacks an inherited package: " + name)
        require(isinstance(expected_control, dict) and
                expected_control == dependencies.control_fields(final_packages[name]),
                "replacement does not match inherited control metadata: " + name)
    replaced_inherited = []
    for name, retained in retained_packages.items():
        require(isinstance(retained, dict) and isinstance(retained.get("control"), dict),
                "retained package lacks Debian control metadata: " + name)
        record = dependencies.package_from_fields(retained["control"], origin="Make retained " + name)
        require(record.name == name, "retained package name differs from control metadata: " + name)
        control = dependencies.control_fields(record)
        if name in replacements:
            require(control != replacements[name], "replacement does not change inherited metadata: " + name)
            replaced_inherited.append({"package": name, "before": replacements[name],
                                       "after": control, "source_sha256": retained["source_sha256"]})
        else:
            require(name not in final_packages or control == dependencies.control_fields(final_packages[name]),
                    "retained package metadata conflicts with inherited package: " + name)
        final_packages[name] = record
    base_package_count = len(final_packages)
    base_elfs = {name: item for name, item in base_files.items() if "elf_machine" in item}
    selected, skipped_base, skipped_retained, overlaps, duplicates = [], [], [], [], []
    selected_names = {}
    selected_files = {}
    for key, package in sorted(locked.items()):
        path = paths[key]["payload"]
        name = package["name"]
        if name in retained_packages:
            skipped_retained.append({"key": key, "package": name, "source_sha256": retained_packages[name]["source_sha256"]})
            continue
        if name in final_packages and name not in selected_names:
            skipped_base.append({"key": key, "package": name, "selected_version": package["version"],
                                 "base_version": final_packages[name].version})
            continue
        if name in selected_names:
            duplicates.append({"package": name, "kept_key": selected_names[name], "duplicate_key": key})
            continue
        selected_names[name] = key
        final_packages[name] = metadata[key]
        files = inspect_payload(path)
        for filename, item in files.items():
            previous = base_files.get(filename)
            if filename in base_elfs:
                require(previous == item, "APT package " + name + " would replace a base ELF: " + filename)
            if "elf_machine" in selected_files.get(filename, {}):
                require(selected_files[filename] == item, "APT package " + name + " would replace a selected ELF: " + filename)
            elif previous is not None and previous != item and previous["kind"] != "directory":
                overlaps.append({"package": name, "path": filename})
        selected_files.update(files)
        selected.append({"key": key, "package": name, "version": package["version"],
                         "source_sha256": package["sha256"], "payload_sha256": content[key]["payload_sha256"],
                         "control_sha256": content[key]["control_sha256"], "payload_bytes": content[key]["payload_size"], "path": path})
    dependency_check = dependencies.validate(final_packages, architecture=architecture)
    dependency_check.update(
        base_package_inventory_sha256=installed_digest,
        packages={name: dependencies.control_fields(record) for name, record in sorted(final_packages.items())},
    )
    check_overlay(selected_files, base_files)
    receipt = {
        "_generated": {
            "notice": "AUTO-GENERATED. DO NOT EDIT MANUALLY.",
            "generator": "sonic-build-infra/apt/sonic_apt/selection.py",
        },
        "schema": 1, "group": group,
        "dependency_check": dependency_check,
        "base_package_count": base_package_count, "base_elf_count": len(base_elfs),
        "apt_lock_sha256": sha(lock_path),
        "selected": [{key: value for key, value in item.items() if key != "path"} for item in selected],
        "skipped_base": skipped_base, "skipped_retained": skipped_retained,
        "duplicate_sources": duplicates,
        "replaced_inherited": sorted(replaced_inherited, key=lambda item: item["package"]),
        "changed_non_elf_base_paths": sorted(overlaps, key=lambda item: (item["path"], item["package"])),
    }
    return [item["path"] for item in selected], receipt


def stage_payloads(paths, directory):
    """Copy selected archives into one declared directory for upstream flatten.

    Numeric filenames preserve selection order when Bazel expands the directory.
    The empty archive handles an empty selection with the standard flatten API.
    This copies archives intact; it does not extract or merge their contents.
    """
    directory.mkdir(parents=True, exist_ok=True)
    require(not any(directory.iterdir()), "selected archive directory must be empty")
    with tarfile.open(directory / "000000-empty.tar", "w", format=tarfile.GNU_FORMAT):
        pass
    for index, path in enumerate(paths, 1):
        shutil.copyfile(path, directory / (str(index).zfill(6) + ".tar"))
