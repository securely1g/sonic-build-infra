"""Verify locked APT payloads and retain packages supplied by the image base."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


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
    result = {}
    for paragraph in status.decode().strip().split("\n\n"):
        fields = {}
        for line in paragraph.splitlines():
            if line and not line[0].isspace() and ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.lstrip()
        if fields.get("Status") != "install ok installed":
            continue
        require(fields.get("Package") and fields.get("Version") and fields.get("Architecture") in (architecture, "all"),
                "invalid or foreign installed package in OCI base")
        require(fields["Package"] not in result, "duplicate installed package in OCI base")
        result[fields["Package"]] = fields["Version"]
    require(result, "OCI base has no installed packages")
    return result


def select(lock_path, mapping_path, *, group, architecture, installed, base_files,
           retained_packages, inspect_payload, check_overlay):
    """Select verified payloads using the consumer's existing file inventory reader.

    Inventories map relative paths to kind/mode/uid/gid/hash/link/ELF metadata.
    inspect_payload(path) returns that map and rejects unsafe archive entries;
    check_overlay(selected_files, base_files) rejects paths crossing symlinks.
    Retained packages map package names to their source_sha256 identities.
    """
    mapping = json.loads(mapping_path.read_bytes())
    require(mapping.get("architecture") == architecture,
            "APT package set has a foreign target architecture")
    entries = mapping["locked"]
    locked = {item["key"]: item["package"] for item in entries}
    require(len(locked) == len(entries), "duplicate package in APT package set")
    paths = {item["key"]: {kind: Path(item[kind]) for kind in ("payload", "control")} for item in entries}
    # The Distroless provider supplies the checked closure and exact imports.
    # Preserve optional reviewed extracted hashes used by SONiC's Make handoff.
    content = {}
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
        if name in installed:
            skipped_base.append({"key": key, "package": name, "selected_version": package["version"],
                                 "base_version": installed[name]})
            continue
        if name in selected_names:
            duplicates.append({"package": name, "kept_key": selected_names[name], "duplicate_key": key})
            continue
        selected_names[name] = key
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
    check_overlay(selected_files, base_files)
    receipt = {
        "schema": 1, "group": group,
        "base_package_count": len(installed), "base_elf_count": len(base_elfs),
        "apt_lock_sha256": sha(lock_path),
        "selected": [{key: value for key, value in item.items() if key != "path"} for item in selected],
        "skipped_base": skipped_base, "skipped_retained": skipped_retained,
        "duplicate_sources": duplicates,
        "changed_non_elf_base_paths": sorted(overlaps, key=lambda item: (item["path"], item["package"])),
    }
    return [item["path"] for item in selected], receipt
