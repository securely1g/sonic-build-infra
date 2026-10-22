"""Generate a synthetic installed base and owner data, without building packages."""

import argparse
import json
from pathlib import Path
import tempfile

from fixture import archive, layout
from installed_packages import inventory_digest

parser = argparse.ArgumentParser()
for name in ("base", "apt", "policy", "owner", "module", "source"):
    parser.add_argument("--" + name, type=Path, required=True)
parser.add_argument("--architecture", required=True)
args = parser.parse_args()
base_fields = {"Package": "base-files", "Version": "1.0", "Architecture": args.architecture,
               "Status": "install ok installed"}
with tempfile.TemporaryDirectory() as temporary:
    data = "\n".join(key + ": " + value for key, value in base_fields.items()) + "\n\n"
    layer = archive(Path(temporary) / "base.tar", {"var/lib/dpkg/status": data.encode()})
    layout(args.base, [layer], args.architecture)
index = json.loads((args.base / "index.json").read_text())
args.apt.write_text(json.dumps({"base_manifest_digest": index["manifests"][0]["digest"],
    "selected": [], "dependency_check": {"status": "satisfied", "packages": {"base-files": base_fields},
    "base_package_inventory_sha256": inventory_digest({"base-files": base_fields})}}))
args.policy.write_text(json.dumps({"schema": 1, "packages": {
    "example": {"required_paths": ["usr/share/example/data"]}}}))
args.owner.write_text(json.dumps({"schema": 1, "module": "example", "packages": {
    "example": {"runtime_target": "//oci/tests:metadata_source.tar", "control_fields": {
        "Package": "example", "Version": "2.0", "Depends": "base-files (>= 1.0)",
        "Description": "Source-owned package"}}}}))
args.module.write_text('module(name="example", version="1.0.0.sonic.1")\n')
archive(args.source, {"usr/share/example/data": b"source payload\n"})
