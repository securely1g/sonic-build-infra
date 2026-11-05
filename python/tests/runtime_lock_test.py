"""Runtime-lock contract from the user-approved dependency handoff and PEP 508."""

import copy
import hashlib
import json
import io
import os
import stat
import tarfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime_lock

from runtime_lock import (
    RuntimeLockError, canonical_json, check, digest, external_requests,
    installed_distributions, make_plan, requirements_text, target_environment,
    validate_lock, verify_installation,
)


def source(name, version="1.2", requires=(), extras=(), python=">=3.11"):
    return {"distribution": name, "version": version, "requires_dist": list(requires),
            "requires_python": python, "extras": list(extras), "producer": "@source//:" + name}


def plan(sources=(), roots=("example-service[metrics]",), **changes):
    value = {"schema_version": 1, "roots": list(roots), "sources": list(sources),
             "python_version": "3.13", "python_platform": "x86_64-unknown-linux-gnu",
             "uv_version": "0.12.18", "index_urls": [], "find_links": [], "no_index": True,
             "constraints": [], "marker_environment": {}, "source_distributions": []}
    value.update(changes)
    requests, selected = external_requests(value)
    value.update(external_requirements=requests, selected_sources=selected)
    return value


class RuntimePlanTest(unittest.TestCase):
    def test_transitive_source_extras_and_target_markers(self):
        value = plan([
            source("example-service", requires=["common[fast]>=1", "requests>=2; python_version >= '3.12'",
                                                "monitor>=3; extra == 'metrics'", "win-only; sys_platform == 'win32'"] ,extras=["metrics"]),
            source("common", requires=["PyYAML>=6", "speed>=2; extra == 'fast'", "arm-only; platform_machine == 'aarch64'"], extras=["fast"]),
        ])
        self.assertEqual(value["selected_sources"], ["common", "example-service"])
        self.assertEqual(value["external_requirements"], ["PyYAML>=6", "monitor>=3", "requests>=2", "speed>=2"])
        arm = copy.deepcopy(value)
        arm["python_platform"] = "aarch64-unknown-linux-gnu"
        self.assertIn("arm-only", external_requests(arm)[0])
        older = copy.deepcopy(value)
        older["python_version"] = "3.11"
        self.assertNotIn("requests>=2", external_requests(older)[0])

    def test_diamond_and_incremental_extra_selection(self):
        value = plan([
            source("root", requires=["left", "right"]),
            source("left", requires=["common[a]"]),
            source("right", requires=["common[b]"]),
            source("common", requires=["first; extra == 'a'", "second; extra == 'b'"], extras=["a", "b"]),
        ], roots=["root"])
        self.assertEqual(value["external_requirements"], ["first", "second"])
        self.assertEqual(value["selected_sources"], ["common", "left", "right", "root"])

    def test_source_conflict_missing_producer_and_python_constraint(self):
        with self.assertRaisesRegex(RuntimeLockError, "does not satisfy"):
            plan([source("root", requires=["common>=2"]), source("common", version="1.2")], roots=["root"])
        with self.assertRaisesRegex(RuntimeLockError, "missing source producer"):
            plan([source("root", requires=["common"])], roots=["root"], source_distributions=["common"])
        with self.assertRaisesRegex(RuntimeLockError, "requires Python"):
            plan([source("root", python=">=3.14")], roots=["root"])
        with self.assertRaisesRegex(RuntimeLockError, "does not declare extras"):
            plan([source("root")], roots=["root[unknown]"])
        with self.assertRaisesRegex(RuntimeLockError, "conflicting source producer"):
            plan([source("root", version="1"), source("root", version="2")], roots=["root"])

    def test_raw_metadata_change_invalidates_even_when_export_is_equal(self):
        first = plan([source("root", requires=["common>=1"]), source("common")], roots=["root"])
        second = plan([source("root", requires=["common>=1.1"]), source("common")], roots=["root"])
        self.assertEqual(requirements_text(first), requirements_text(second))
        self.assertNotEqual(digest(canonical_json(first)), digest(canonical_json(second)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            req, lock, inputs = root / "requirements.in", root / "requirements_lock.txt", root / "lock-inputs.json"
            req.write_text(requirements_text(first))
            lock.write_text("")
            inputs.write_text(json.dumps({"schema_version": 1, "plan_sha256": digest(canonical_json(first)),
                                         "requirements_sha256": digest(req.read_bytes()), "lock_sha256": digest(lock.read_bytes())}))
            before = [path.read_bytes() for path in (req, lock, inputs)]
            check(first, req, lock, inputs)
            with self.assertRaisesRegex(RuntimeLockError, "stale"):
                check(second, req, lock, inputs)
            self.assertEqual(before, [path.read_bytes() for path in (req, lock, inputs)])

    def test_plan_metadata_paths_are_not_part_of_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = source("root", requires=["idna>=3"])
            raw.pop("producer")
            a, b = root / "a.json", root / "b.json"
            for path in (a, b): path.write_text(json.dumps(raw))
            manifest = {key: value for key, value in plan([], roots=[]).items()
                        if key not in ("schema_version", "sources", "external_requirements", "selected_sources")}
            manifest.update(roots=["root"], metadata=[{"path": str(a), "producer": "@root//:runtime"}], constraints=[])
            first = make_plan(manifest)
            manifest["metadata"][0]["path"] = str(b)
            self.assertEqual(first, make_plan(manifest))
            # Resource bytes are deliberately absent from the dependency plan.
            (root / "resource.json").write_text('{"new": true}')
            self.assertEqual(first, make_plan(manifest))

    def test_lock_requires_real_hashes_exact_versions_and_request_coverage(self):
        value = plan([], roots=["idna>=3"])
        with self.assertRaisesRegex(RuntimeLockError, "no SHA256"):
            validate_lock(value, "idna==3.10\n")
        hashed = " --hash=sha256:" + hashlib.sha256(b"artifact fixture").hexdigest()
        with self.assertRaisesRegex(RuntimeLockError, "exact version"):
            validate_lock(value, "idna>=3" + hashed)
        with self.assertRaisesRegex(RuntimeLockError, "lacks required"):
            validate_lock(value, "")
        self.assertEqual(validate_lock(value, "idna==3.10" + hashed)["idna"]["version"], "3.10")

    def test_installed_closure_matches_lock_and_checks_transitive_metadata(self):
        value = plan([source("root", requires=["outer>=1"])], roots=["root"])
        hashed = " --hash=sha256:" + "a" * 64
        lock = "outer==1.0" + hashed + "\ninner==2.0" + hashed + "\n"
        with tempfile.TemporaryDirectory() as directory:
            def archive(name, metadata):
                path = Path(directory) / (name + ".tar")
                with tarfile.open(path, "w") as output:
                    for distribution, body in metadata.items():
                        contents = body.encode()
                        item = tarfile.TarInfo("site/" + distribution + ".dist-info/METADATA")
                        item.size = len(contents)
                        output.addfile(item, io.BytesIO(contents))
                return path
            root = archive("root", {"root": "Name: root\nVersion: 1.2\nRequires-Python: >=3.11\nRequires-Dist: outer>=1\n"})
            external = archive("external", {
                "outer": "Name: outer\nVersion: 1.0\nRequires-Dist: inner>=2\n",
                "inner": "Name: inner\nVersion: 2.0\n"})
            verify_installation(value, lock, [root], [external])
            with self.assertRaisesRegex(RuntimeLockError, "third-party installation set"):
                verify_installation(value, lock, [root], [])
            wrong = archive("wrong", {"outer": "Name: outer\nVersion: 1.1\n", "inner": "Name: inner\nVersion: 2.0\n"})
            with self.assertRaisesRegex(RuntimeLockError, "version differs"):
                verify_installation(value, lock, [root], [wrong])
            broken = archive("broken", {"outer": "Name: outer\nVersion: 1.0\nRequires-Dist: absent>=1\n", "inner": "Name: inner\nVersion: 2.0\n"})
            with self.assertRaisesRegex(RuntimeLockError, "lacks transitive requirements"):
                verify_installation(value, lock, [root], [broken])

    def test_target_environment_does_not_inherit_host_markers(self):
        env = target_environment("3.13", "aarch64-unknown-linux-gnu")
        self.assertEqual((env["python_full_version"], env["platform_machine"], env["sys_platform"]), ("3.13.0", "aarch64", "linux"))
        self.assertEqual(env["platform_release"], "")

    def test_new_lock_outputs_are_world_readable_with_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs = {Path(directory) / name: name.encode() for name in ("requirements.in", "requirements_lock.txt", "lock-inputs.json")}
            prior_umask = os.umask(0o077)
            try:
                runtime_lock._publish(outputs)
            finally:
                os.umask(prior_umask)
            for path, contents in outputs.items():
                self.assertEqual((path.read_bytes(), stat.S_IMODE(path.stat().st_mode)), (contents, 0o644))

    def test_updated_lock_outputs_preserve_existing_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs = {Path(directory) / name: permission for name, permission in [
                ("requirements.in", 0o444), ("requirements_lock.txt", 0o640), ("lock-inputs.json", 0o660)]}
            for path, permission in outputs.items():
                path.write_bytes(b"old input")
                path.chmod(permission)
            runtime_lock._publish({path: b"new input" for path in outputs})
            for path, permission in outputs.items():
                self.assertEqual((path.read_bytes(), stat.S_IMODE(path.stat().st_mode)), (b"new input", permission))

    def test_partial_publication_failure_restores_readonly_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs = {Path(directory) / name: permission for name, permission in [
                ("requirements.in", 0o444), ("requirements_lock.txt", 0o640), ("lock-inputs.json", 0o660)]}
            for path, permission in outputs.items():
                path.write_bytes(b"old input")
                path.chmod(permission)
            replace = os.replace
            calls = 0
            failed = False

            def fail_later_publication(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise PermissionError("later publication denied")
                return replace(source, destination)

            # Staging and the first publication use the real filesystem. The
            # result must be all-old on error or all-new on success; helper
            # call counts and a specific publication algorithm are not asserted.
            with patch.object(runtime_lock.os, "replace", side_effect=fail_later_publication):
                try:
                    runtime_lock._publish({path: b"new input" for path in outputs})
                except Exception:
                    failed = True
            for path, permission in outputs.items():
                self.assertEqual((path.read_bytes(), stat.S_IMODE(path.stat().st_mode)),
                                 (b"old input" if failed else b"new input", permission))


if __name__ == "__main__":
    unittest.main()
