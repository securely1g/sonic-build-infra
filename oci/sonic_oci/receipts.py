"""Validate source receipts against their independently declared owners."""

import re

CONTROL_FIELDS = {"Package", "Version", "Architecture", "Depends", "Pre-Depends", "Provides", "Multi-Arch"}
SOURCE_FIELDS = {"module", "version", "target"}
RECEIPT_SCHEMA = 2

def require(value, message):
    if not value:
        raise ValueError(message)

def elf_machine(architecture):
    machines = {"amd64": 62, "arm64": 183}
    require(architecture in machines, "unsupported package ELF architecture: " + str(architecture))
    return machines[architecture]

def identity(document):
    """Read the owner identity shared by contracts, policies and receipts."""
    require(isinstance(document, dict) and all(isinstance(document.get(key), str) and document[key]
                for key in ("image", "architecture", "distribution")), "invalid package contract identity")
    elf_machine(document["architecture"])
    return {key: document[key] for key in ("image", "architecture", "distribution")}

def package_identity(record, *, architecture):
    """Validate package controls and commit-pinned public source provenance."""
    require(isinstance(record, dict), "invalid source package record")
    name = record.get("package")
    require(isinstance(name, str) and re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", name) and
            isinstance(record.get("version"), str) and record["version"] and
            record.get("architecture") == architecture, "unsupported source package identity")
    fields = record.get("control_fields")
    require(isinstance(fields, dict) and set(fields) == CONTROL_FIELDS and
            all(isinstance(value, str) for value in fields.values()) and
            (fields["Package"], fields["Version"], fields["Architecture"]) ==
            (name, record["version"], record["architecture"]),
            "invalid source package dependency controls: " + name)
    source = record.get("source")
    source_identity(source, name=name)

def source_identity(source, *, name):
    """Check SONiC commit and foreign release identities through one contract."""
    require(isinstance(source, dict) and set(source) in
            (SOURCE_FIELDS | {"commit"}, SOURCE_FIELDS | {"release"}) and
            all(isinstance(value, str) and value for value in source.values()),
            "invalid source package provenance: " + name)
    if "commit" in source:
        pinned = (re.fullmatch(r"[0-9a-f]{40}", source["commit"]) and
                  source["version"].endswith("-" + source["commit"]))
    else:
        # Foreign packages retain their upstream release and SONiC patch number.
        # Bazel verifies the registry archive/patch integrity; verify_modules
        # below binds this contract to the module actually selected by Bazel.
        pinned = (re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}", source["release"]) and
                  re.fullmatch(re.escape(source["release"]) + r"\.sonic\.[1-9][0-9]*", source["version"]))
    require(re.fullmatch(r"@{1,2}[A-Za-z0-9_.+~-]+//[A-Za-z0-9_./+-]*:[A-Za-z0-9_./+-]+", source["target"]) and pinned,
            "source package does not identify its public owner target: " + name)

def receipt_records(value, contract=None):
    """Check receipt structure and, when supplied, its independently declared package owners."""
    identity(value)
    require(type(value.get("schema")) is int and value["schema"] == RECEIPT_SCHEMA and
            value.get("kind") == "bazel_source" and "debug_payload" not in value,
            "unsupported source package receipt; expected runtime-only schema 2")
    require(re.fullmatch(r"[0-9a-f]{64}", value.get("contract_sha256", "")) and
            re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("base_manifest_digest", "")),
            "source receipt lacks its contract or base identity")
    records = value.get("packages")
    require(isinstance(records, list) and records, "source receipt must contain its source packages")
    names = set()
    for record in records:
        package_identity(record, architecture=value["architecture"])
        require(record["package"] not in names, "duplicate source receipt package: " + record["package"])
        names.add(record["package"])
        require("debug" not in record, "runtime source receipt must not contain debug inputs")
        require(re.fullmatch(r"[0-9a-f]{64}", record.get("input_tar_sha256", "")) and
                isinstance(record.get("files"), dict) and record["files"],
                "source receipt lacks a package tar identity and inventory")
    modules = value.get("module_file_sha256")
    expected_modules = {record["source"]["module"] for record in records}
    require(isinstance(modules, dict) and set(modules) == expected_modules and
            all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) for digest in modules.values()),
            "source receipt lacks its resolved module identities")
    if contract is not None:
        require(identity(value) == identity(contract), "source receipt differs from the contract identity")
        expected_records = {record["package"]: record for record in contract["packages"]}
        require(names == set(expected_records), "source receipt differs from the declared package owners")
        for record in records:
            expected = expected_records[record["package"]]
            require(all(record[key] == expected[key] for key in
                        ("package", "version", "architecture", "control_fields", "source")),
                    "source receipt package differs from the reviewed contract: " + record["package"])
    return records
