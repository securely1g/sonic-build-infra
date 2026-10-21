"""Bind owner package declarations to the actual selected module and TAR labels."""

import ast
import hashlib
import json
import re


def require(value, message):
    if not value:
        raise ValueError(message)


def module_identity(path, expected):
    calls = [n.value for n in ast.parse(path.read_text()).body
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
             and isinstance(n.value.func, ast.Name) and n.value.func.id == "module"]
    require(len(calls) == 1, "expected one module declaration: " + expected)
    fields = {item.arg: ast.literal_eval(item.value) for item in calls[0].keywords if item.arg in ("name", "version")}
    require(fields.get("name") == expected, "owner module declaration differs: " + expected)
    version = fields.get("version")
    require(version or expected == "sonic-buildimage", "external owner module has no version: " + expected)
    return version or "workspace"


def resolve(spec, owners, modules, source_labels, architecture):
    """Use owners for dependency facts; keep installed-path policy in the image."""
    require(spec.get("schema") == 1, "unsupported image source policy schema")
    expected = spec.get("packages")
    require(isinstance(expected, dict) and set(expected) == set(source_labels), "image paths differ from source TAR declarations")
    module_versions = {name: module_identity(path, name) for name, path in modules.items()}
    records, identities = {}, {}
    for label, path in owners.items():
        owner = json.loads(path.read_text())
        module = owner.get("module")
        require(owner.get("schema") == 1 and module in module_versions, "owner metadata lacks its selected module")
        repository = label.split("//", 1)[0]
        require("//" in label and isinstance(owner.get("packages"), dict), "invalid owner metadata label or packages")
        for name, entry in owner["packages"].items():
            if name not in source_labels:
                continue
            require(name not in records, "multiple source owners declare package: " + name)
            target = entry.get("runtime_target", "")
            require(target.startswith("//") and ":" in target and source_labels[name] == repository + target,
                    "source TAR does not match its owner runtime target: " + name)
            control = entry.get("control_fields")
            require(isinstance(control, dict) and control.get("Package") == name and control.get("Version") and
                    all(isinstance(k, str) and isinstance(v, str) for k, v in control.items()),
                    "invalid owner package controls: " + name)
            require(not set(control).intersection({"Architecture", "Status", "Installed-Size", "Conffiles", "SHA256", "MD5sum"}),
                    "owner metadata must not freeze installed architecture or payload state: " + name)
            require(set(expected[name]) <= {"required_paths", "path_modes", "path_owners", "required_inherited_files"},
                    "image policy duplicates owner package metadata: " + name)
            version = module_versions[module]
            commit = re.search(r"-([0-9a-f]{40})$", version)
            revision = commit[1] if commit else version
            records[name] = {**expected[name], "control_fields": {**control, "Architecture": architecture},
                             "target": source_labels[name], "source_revision": revision, "source_module": module}
            identities[module] = {"version": version, "module_sha256": hashlib.sha256(modules[module].read_bytes()).hexdigest()}
    require(set(records) == set(expected), "missing source owner package metadata")
    return {**spec, "packages": records}, identities
