"""Validate installed behavior and the public image dependency handoff.

Contract: this task's agreed source-installation TAR interface and Python's
installed distribution metadata. Expectations come from the fixture package
and wheel metadata, not the implementation's path/metadata helpers.
"""
import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import posixpath
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


class InstalledPackageTest(unittest.TestCase):
    def test_installed_package_and_metadata_match_wheel(self):
        with tarfile.open(ARGS.demo) as archive:
            payload = {member.name.removeprefix("./"): archive.extractfile(member).read()
                       for member in archive if member.isfile()}
            generated = archive.getmember("./opt/runtime/site/site_demo/generated.txt")
            self.assertEqual(generated.mode, 0o644)
            self.assertEqual(archive.extractfile(generated).read(), b"generated package data\n")
            site = "opt/runtime/site"
            metadata_path = site + "/site_demo-2.4.1.dist-info/METADATA"
            installed = BytesParser().parsebytes(payload[metadata_path])
            with zipfile.ZipFile(ARGS.wheel) as wheel:
                original = BytesParser().parsebytes(wheel.read("site_demo-2.4.1.dist-info/METADATA"))
            for field in ("Name", "Version", "Requires-Python", "Summary", "Requires-Dist", "Provides-Extra",
                          "Classifier", "Project-URL", "Description-Content-Type",
                          "Metadata-Version", "Maintainer", "Maintainer-email", "Keywords", "License-File"):
                self.assertEqual(installed.get_all(field), original.get_all(field), field)
            self.assertEqual(installed["Metadata-Version"], "2.4")
            self.assertEqual(installed["Maintainer"], "Fixture Maintainer")
            self.assertEqual(installed["Maintainer-email"], "maintainer@example.org")
            self.assertEqual(installed["Keywords"], "network,metadata")
            self.assertEqual(installed.get_all("License-File"), ["LICENSE"])
            with zipfile.ZipFile(ARGS.wheel) as wheel:
                for member in wheel.infolist():
                    if not member.is_dir():
                        self.assertEqual((member.external_attr >> 16) & 0o777, 0o644, member.filename)
            self.assertEqual(installed.get_payload(decode=True).rstrip(b"\n"), original.get_payload(decode=True).rstrip(b"\n"))
            self.assertEqual(installed.get_payload(decode=True).decode("utf-8"), "# Source distribution\n\nUTF-8 documentation: café.\n")
            self.assertEqual(installed.get_all("Classifier"), ["Programming Language :: Python :: 3", "Topic :: Software Development"])
            self.assertEqual(set(installed.get_all("Project-URL")), {"Documentation, https://example.org/docs", "Source, https://example.org/source"})
            self.assertEqual(payload[site + "/site_demo-2.4.1.dist-info/licenses/LICENSE"], b"Fixture license terms.\n")
            self.assertEqual(installed["Name"], "site-demo")
            self.assertEqual(installed["Version"], "2.4.1")
            self.assertFalse(any("leaf_package" in name for name in payload))
            self.assertTrue(payload["opt/runtime/bin/site-demo"].startswith(b"#!/usr/bin/python3\n"))
            record_path = site + "/site_demo-2.4.1.dist-info/RECORD"
            rows = list(csv.reader(io.StringIO(payload[record_path].decode())))
            paths = set()
            for name, digest, size in rows:
                name = posixpath.normpath(posixpath.join(site, name))
                paths.add(name)
                if name == record_path:
                    self.assertEqual((digest, size), ("", ""))
                else:
                    expected = base64.urlsafe_b64encode(hashlib.sha256(payload[name]).digest()).rstrip(b"=").decode()
                    self.assertEqual((digest, size), ("sha256=" + expected, str(len(payload[name]))))
            self.assertEqual(paths, set(payload))
            for member in archive:
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
            with tempfile.TemporaryDirectory() as directory:
                archive.extractall(directory)
                installed_site = str(Path(directory) / site)
                launcher = Path(directory) / "opt/runtime/bin/site-demo"
                self.assertTrue(launcher.stat().st_mode & 0o111)
                program = ("import sys; sys.path.insert(0, sys.argv[1]); "
                           "from importlib.metadata import distribution; "
                           "d=distribution('site-demo'); assert d.version == '2.4.1'; "
                           "from site_demo.cli import main; main()")
                result = subprocess.run([sys.executable, "-I", "-c", program, installed_site],
                                        check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout.strip(), '{"installed": true}')
                # Execute the generated console entrypoint against the installed tree.
                program = "import sys,runpy; sys.path.insert(0,sys.argv[1]); runpy.run_path(sys.argv[2],run_name='__main__')"
                result = subprocess.run([sys.executable, "-I", "-c", program, installed_site, str(launcher)],
                                        check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout.strip(), '{"installed": true}')

    def test_three_level_runtime_closure_preserves_requirements(self):
        metadata = [json.loads(Path(path).read_text()) for path in ARGS.metadata]
        packages = {item["distribution"]: item for item in metadata}
        self.assertEqual(set(packages), {"site-demo", "leaf-package", "root-package"})
        self.assertEqual(len(metadata), 3)  # diamond dependency is exported once
        self.assertEqual(len(ARGS.tars), 3)
        self.assertEqual(packages["site-demo"]["requires_dist"], [
            "leaf-package>=1.0; python_version >= '3.11'", "monitor>=2; extra == 'metrics'"])
        self.assertEqual(packages["site-demo"]["requires_python"], ">=3.11")
        self.assertEqual(packages["root-package"]["requires_dist"], ["site-demo[metrics]>=2.4", "leaf-package>=1.0"])
        self.assertEqual(packages["leaf-package"]["requires_dist"], [])
        identities = set()
        for path in ARGS.tars:
            with tarfile.open(path) as archive:
                for member in archive:
                    if member.name.endswith(".dist-info/METADATA"):
                        identities.add(BytesParser().parsebytes(archive.extractfile(member).read())["Name"])
        self.assertEqual(identities, set(packages))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for key in ("demo", "wheel", "tars", "metadata"):
        parser.add_argument("--" + key, required=True, **({"nargs": "+"} if key in ("tars", "metadata") else {}))
    ARGS, extra = parser.parse_known_args()
    unittest.main(argv=[sys.argv[0]] + extra)
