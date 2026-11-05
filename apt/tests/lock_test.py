"""APT lock contract: one immutable package graph, owned requests and safe update.

Expectations follow the user-approved source/lock design and explicit fixture
identities, independently of the resolver and implementation constants.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import lock


def source_document(uri="https://snapshot.example/debian/20260727"):
    return {"version": 1, "sources": [{"uris": [uri], "suites": ["stable"],
                                      "components": ["main"], "architectures": ["amd64", "arm64"]}]}


def request_document(packages=None):
    return {"version": 1, "dependency_sets": {
        "runtime": {"packages": packages or ["demo (>= 1)"], "suites": ["stable"], "include_transitive": True}}}


def package(name="demo", version="1", architecture="amd64", dependencies=None):
    return {"name": name, "version": version, "architecture": architecture, "suite": "stable",
            "filename": f"pool/{name}_{version}_{architecture}.deb", "sha256": "a" * 64,
            "depends_on": dependencies or [], "files": [f"usr/lib/{name}.so"]}


def graph():
    return {"version": 2,
            "sources": {"stable": {"uris": ["https://snapshot.example/debian/20260727"],
                                     "components": ["main"], "architectures": ["amd64", "arm64"], "types": []}},
            "packages": {"/stable/demo:amd64=1": package(dependencies=["/stable/helper:amd64=2"]),
                         "/stable/helper:amd64=2": package("helper", "2")},
            "dependency_sets": {"runtime": {"sets": {"amd64": {"/stable/demo:amd64": "1"}}}},
            "facts": {"stable/main/amd64/Packages/index": "sha256-example"}}


class LockContractTest(unittest.TestCase):
    def finalized(self):
        return lock.finalize_result(graph(), graph(), source_document(), request_document(), "fixture-resolver")

    def test_merge_deduplicates_identity_and_preserves_extraction_metadata(self):
        first, second = graph(), graph()
        second["packages"]["/stable/demo:amd64=1"]["payload_sha256"] = "b" * 64
        second["dependency_sets"] = {"tools": second["dependency_sets"]["runtime"]}
        merged = lock.merge_locks([first, second])
        self.assertEqual(len(merged["packages"]), 2)
        self.assertEqual(set(merged["dependency_sets"]), {"runtime", "tools"})
        self.assertEqual(merged["packages"]["/stable/demo:amd64=1"]["payload_sha256"], "b" * 64)

    def test_merge_rejects_conflicting_identity_hash(self):
        other = graph()
        other["packages"]["/stable/demo:amd64=1"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "conflicting package sha256"):
            lock.merge_locks([graph(), other])

    def test_missing_transitive_package_is_rejected(self):
        broken = graph()
        del broken["packages"]["/stable/helper:amd64=2"]
        with self.assertRaisesRegex(ValueError, "missing from lock"):
            lock.validate_lock(broken)

    def test_changed_requests_stale_even_if_versions_still_satisfy(self):
        locked = self.finalized()
        lock.check_lock(locked, source_document(), request_document(), "fixture-resolver")
        with self.assertRaisesRegex(ValueError, "stale"):
            lock.check_lock(locked, source_document(), request_document(["demo (>= 0.9)"]), "fixture-resolver")

    def test_changed_source_stales_lock(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            lock.check_lock(self.finalized(), source_document("https://snapshot.example/debian/20260801"), request_document(), "fixture-resolver")

    def test_new_version_cannot_silently_replace_existing(self):
        candidate = graph()
        del candidate["packages"]["/stable/helper:amd64=2"]
        candidate["packages"]["/stable/helper:amd64=3"] = package("helper", "3")
        candidate["packages"]["/stable/demo:amd64=1"]["depends_on"] = ["/stable/helper:amd64=3"]
        with self.assertRaisesRegex(ValueError, "version changed"):
            lock.finalize_result(candidate, graph(), source_document(), request_document(), "fixture-resolver")

    def test_existing_payload_metadata_survives_new_export(self):
        previous = graph()
        previous["packages"]["/stable/demo:amd64=1"]["control_sha256"] = "b" * 64
        result = lock.finalize_result(graph(), previous, source_document(), request_document(), "fixture-resolver")
        self.assertEqual(result["packages"]["/stable/demo:amd64=1"]["control_sha256"], "b" * 64)

    def test_source_change_requires_explicit_authorization(self):
        candidate = graph()
        candidate["sources"]["stable"]["uris"] = ["https://snapshot.example/debian/20260801"]
        sources = source_document("https://snapshot.example/debian/20260801")
        with self.assertRaisesRegex(ValueError, "allow-source-change"):
            lock.finalize_result(candidate, graph(), sources, request_document(), "fixture-resolver")
        result = lock.finalize_result(candidate, graph(), sources, request_document(), "fixture-resolver", allow_source_change=True)
        self.assertEqual(result["packages"]["/stable/demo:amd64=1"]["version"], "1")

    def test_duplicate_suite_conflicts_fail(self):
        document = source_document()
        document["sources"].append(dict(document["sources"][0], uris=["https://different.example/"]))
        with self.assertRaisesRegex(ValueError, "conflicting source"):
            lock.source_records(document)

    def test_unrelated_existing_package_is_preserved(self):
        previous = graph()
        previous["packages"]["/stable/owned_elsewhere:amd64=9"] = package("owned_elsewhere", "9")
        result = lock.finalize_result(graph(), previous, source_document(), request_document(), "fixture-resolver")
        self.assertEqual(result["packages"]["/stable/owned_elsewhere:amd64=9"], previous["packages"]["/stable/owned_elsewhere:amd64=9"])

    def test_new_dependency_order_is_stable_from_first_update(self):
        previous = graph()
        alpha, zeta = "/stable/alpha:amd64=1", "/stable/zeta:amd64=1"
        owner, added = "/stable/owned_elsewhere:amd64=9", "/stable/added:amd64=1"
        previous["packages"].update({alpha: package("alpha"), zeta: package("zeta"),
                                     owner: package("owned_elsewhere", "9", dependencies=[zeta, alpha])})
        candidate = graph()
        candidate["packages"].update({alpha: package("alpha"), zeta: package("zeta"),
                                      added: package("added", dependencies=[zeta, "/stable/helper:amd64=2", alpha])})
        candidate["dependency_sets"]["runtime"]["sets"]["amd64"]["/stable/added:amd64"] = "1"
        requests = request_document(["demo (>= 1)", "added (= 1)"])
        first = lock.finalize_result(candidate, previous, source_document(), requests, "fixture-resolver")
        permuted = deepcopy(candidate)
        permuted["packages"][added]["depends_on"].reverse()
        other_first = lock.finalize_result(permuted, previous, source_document(), requests, "fixture-resolver")
        self.assertEqual(first, other_first)
        # An unselected reviewed record retains all fields, including its
        # existing dependency array order, rather than being normalized again.
        self.assertEqual(first["packages"][owner], previous["packages"][owner])
        repeated = lock.finalize_result(permuted, first, source_document(), requests, "fixture-resolver")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apt.lock.json"
            lock.atomic_write(path, first)
            initial_bytes = path.read_bytes()
            lock.atomic_write(path, repeated)
            self.assertEqual(path.read_bytes(), initial_bytes)
        self.assertEqual(repeated["packages"][owner], previous["packages"][owner])

    def test_new_lock_is_world_readable_with_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apt.lock.json"
            prior_umask = os.umask(0o077)
            try:
                lock.atomic_write(path, self.finalized())
            finally:
                os.umask(prior_umask)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            lock.check_lock(json.loads(path.read_text()), source_document(), request_document(), "fixture-resolver")

    def test_update_preserves_existing_lock_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            for permission in (0o640, 0o444, 0o660):
                with self.subTest(permission=oct(permission)):
                    path = Path(directory) / oct(permission)
                    path.write_text("old input")
                    path.chmod(permission)
                    lock.atomic_write(path, self.finalized())
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), permission)
                    lock.check_lock(json.loads(path.read_text()), source_document(), request_document(), "fixture-resolver")

    def test_publication_failure_preserves_readonly_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apt.lock.json"
            original = b"old reviewed lock"
            path.write_bytes(original)
            path.chmod(0o444)
            failed = False
            # The writer performs its actual staging writes. Only the OS
            # publication operation fails; equivalent atomic implementations
            # that avoid it may succeed, but must then publish the full result.
            with patch.object(lock.os, "replace", side_effect=PermissionError("publication denied")):
                try:
                    lock.atomic_write(path, self.finalized())
                except Exception:
                    failed = True
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
            if failed:
                self.assertEqual(path.read_bytes(), original)
            else:
                lock.check_lock(json.loads(path.read_text()), source_document(), request_document(), "fixture-resolver")

    def test_unreachable_package_is_removed(self):
        candidate = graph()
        candidate["packages"]["/stable/orphan:amd64=9"] = package("orphan", "9")
        result = lock.finalize_result(candidate, graph(), source_document(), request_document(), "fixture-resolver")
        self.assertNotIn("/stable/orphan:amd64=9", result["packages"])

    def test_check_does_not_write_and_failed_update_preserves_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in [("sources.json", source_document()), ("requests.json", request_document()),
                               ("apt.lock.json", self.finalized())]:
                (root / name).write_text(json.dumps(data))
            before = (root / "apt.lock.json").read_bytes()
            (root / "apt.lock.json").chmod(0o640)
            before_mode = stat.S_IMODE((root / "apt.lock.json").stat().st_mode)
            command = [sys.executable, str(Path(lock.__file__)), "check", "--sources", str(root / "sources.json"),
                       "--requests", str(root / "requests.json"), "--lock", str(root / "apt.lock.json"),
                       "--resolver-version", "fixture-resolver"]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((root / "apt.lock.json").read_bytes(), before)
            self.assertEqual(stat.S_IMODE((root / "apt.lock.json").stat().st_mode), before_mode)
            candidate = graph()
            del candidate["packages"]["/stable/helper:amd64=2"]
            (root / "bad-export.json").write_text(json.dumps(candidate))
            command[2] = "update"
            result = subprocess.run(command + ["--resolved-lock", str(root / "bad-export.json")], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "apt.lock.json").read_bytes(), before)
            self.assertEqual(stat.S_IMODE((root / "apt.lock.json").stat().st_mode), before_mode)

    def test_fixed_archive_hash_is_checked(self):
        requests = request_document()
        requests["pinned_packages"] = [{"suite": "stable", "name": "demo", "architecture": "amd64", "version": "1", "sha256": "b" * 64}]
        with self.assertRaisesRegex(ValueError, "fixed DEB identity"):
            lock.finalize_result(graph(), graph(), source_document(), requests, "fixture-resolver")

    def test_suite_partitions_join_updates_without_mixing_distributions(self):
        sources = source_document()
        sources["sources"][0]["suites"] = ["stable", "stable-updates", "oldstable"]
        requests = request_document()
        requests["dependency_sets"].update({
            "updates": dict(requests["dependency_sets"]["runtime"], suites=["stable", "stable-updates"]),
            "oldtools": dict(requests["dependency_sets"]["runtime"], suites=["oldstable"]),
        })
        partitions = lock.resolution_partitions(sources, requests)
        self.assertEqual({frozenset(suites) for suites, _, _ in partitions},
                         {frozenset(["stable", "stable-updates"]), frozenset(["oldstable"])})
        for suites, selected_sources, selected_requests in partitions:
            self.assertEqual(set(lock.source_records(selected_sources)), suites)
            for request in selected_requests["dependency_sets"].values():
                self.assertLessEqual(set(request["suites"]), suites)

    def test_unscoped_request_retains_selection_from_all_configured_suites(self):
        sources = source_document()
        sources["sources"][0]["suites"].append("oldstable")
        requests = request_document()
        requests["dependency_sets"]["runtime"]["suites"] = []
        self.assertEqual(lock.resolution_partitions(sources, requests)[0][0], {"stable", "oldstable"})

    def test_unknown_request_suite_is_rejected_before_resolution(self):
        requests = request_document()
        requests["dependency_sets"]["runtime"]["suites"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "unknown source suites"):
            lock.resolution_partitions(source_document(), requests)

    def test_equal_package_versions_keep_distribution_hashes_and_native_filemaps(self):
        sources, requests, previous = source_document(), request_document(), graph()
        sources["sources"].append(dict(sources["sources"][0], suites=["oldstable"]))
        requests["dependency_sets"]["oldtools"] = dict(requests["dependency_sets"]["runtime"], suites=["oldstable"])
        calls = []

        def export(partition_sources, partition_requests, partition_previous, options):
            suites = set(lock.source_records(partition_sources))
            self.assertEqual(len(suites), 1, "mixed live indexes overwrite equal versions")
            suite = next(iter(suites))
            self.assertTrue(all(record["suite"] == suite for record in partition_previous["packages"].values()))
            calls.append(options.work_dir)
            result = graph()
            if suite == "oldstable":
                result = json.loads(json.dumps(result).replace("stable", "oldstable").replace('"runtime"', '"oldtools"'))
                for record in result["packages"].values():
                    record["sha256"] = "b" * 64
                    record["files"] = ["usr/include/oldstable/" + record["name"] + ".h"]
            result["_source_document"] = partition_sources
            result["_module_requests"] = partition_requests
            return result

        with tempfile.TemporaryDirectory() as directory, patch.object(lock, "resolve_partition", side_effect=export):
            candidate = lock.resolve(sources, requests, previous, SimpleNamespace(work_dir=directory))
        self.assertEqual(len(set(calls)), 2)
        result = lock.finalize_result(candidate, previous, sources, requests, "fixture-resolver")
        for key, record in previous["packages"].items():
            self.assertEqual(result["packages"][key], record)
        self.assertEqual(result["packages"]["/oldstable/demo:amd64=1"]["sha256"], "b" * 64)
        self.assertEqual(result["packages"]["/oldstable/demo:amd64=1"]["files"], ["usr/include/oldstable/demo.h"])
        self.assertEqual(set(result["_inputs"]["module_requests_document"]["dependency_sets"]), {"runtime", "oldtools"})
        lock.check_lock(result, sources, requests, "fixture-resolver")


if __name__ == "__main__":
    unittest.main()
