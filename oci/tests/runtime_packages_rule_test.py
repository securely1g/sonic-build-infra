"""Verify real ELF inputs produce Debian controls without a DEB producer.

The shared library calls libc; the executable calls that library. Their resolved
dependencies must follow the target binaries, including source-package versions
read from the changelog, and metadata must identify the exact supplied TARs.
"""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


metadata, library, program, generator, tools, tar, control, changelog = map(Path, sys.argv[1:9])
architecture = sys.argv[9]
patchelf = Path(sys.argv[10])
libc_data, libc_control = map(Path, sys.argv[11:13])


class RuntimePackagesRuleTest(unittest.TestCase):
    def generate(self, root, source_control, payload, package="metadata-library", additional=()):
        return subprocess.run([
            str(generator.resolve()), "--control", str(source_control),
            "--changelog", str(changelog.resolve()), "--module", "sonic-build-infra",
            "--architecture", architecture, "--tools", str(tools.resolve()),
            "--tar", str(tar.resolve()), "--output", str(root / "metadata.json"),
            "--tree", str(root / "packages"),
            "--package", package + "=//oci/tests:metadata_library_tar=" + str(payload.resolve()),
            # Deliberately no target APT package or source dependency.
            *additional,
        ], text=True, capture_output=True)

    def control(self, root, package, relationships, architecture="any"):
        source_control = root / "control"
        source_control.write_text(
            "Source: metadata-example\nMaintainer: Example <example@example.org>\n"
            "Section: libs\nPriority: optional\nBuild-Depends: debhelper-compat (= 13)\n\n"
            "Package: " + package + "\nArchitecture: " + architecture + "\n"
            + relationships + "\nDescription: Generated metadata helper fixture.\n")
        return source_control

    def test_existing_debian_fields_and_actual_library_dependencies(self):
        document = json.loads(metadata.read_text())
        packages = document["packages"]
        self.assertEqual(set(packages), {"metadata-library", "metadata-cli"})
        for name, item in packages.items():
            with self.subTest(package=name):
                fields = item["control_fields"]
                self.assertEqual(fields["Package"], name)
                self.assertEqual(fields["Version"], "2.0")
                self.assertEqual(fields["Maintainer"], "Example <example@example.org>")
                self.assertNotIn("${", json.dumps(fields))
                self.assertNotIn("Architecture", fields)
        self.assertIn("libc6 (>=", packages["metadata-library"]["control_fields"]["Depends"])
        self.assertIn("metadata-library (>= 2.0)", packages["metadata-cli"]["control_fields"]["Depends"])
        self.assertEqual(packages["metadata-library"]["runtime_target"], "//oci/tests:metadata_library_tar")
        self.assertEqual(packages["metadata-cli"]["runtime_target"], "//oci/tests:metadata_cli_tar")
        self.assertEqual(packages["metadata-library"]["payload_sha256"], hashlib.sha256(library.read_bytes()).hexdigest())
        self.assertEqual(packages["metadata-cli"]["payload_sha256"], hashlib.sha256(program.read_bytes()).hexdigest())

    def test_missing_target_library_cannot_fall_back_to_execution_runtime(self):
        """Declared dpkg tools include libc, but that libc cannot satisfy a target dependency."""
        self.assertTrue(list((tools / "usr/lib").glob("*/libc.so.6")))
        for remove_depends in (False, True):
            with self.subTest(control_has_depends=not remove_depends), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source_control = root / "control"
                source_control.write_text("\n".join(
                    line for line in control.read_text().splitlines()
                    if not remove_depends or not line.startswith("Depends:")) + "\n")
                result = self.generate(root, source_control, library)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertRegex(result.stderr, r"(?:cannot|couldn't) find library libc\.so\.6")
                self.assertFalse((root / "metadata.json").exists())

    def test_absolute_rpath_cannot_escape_declared_target_package_roots(self):
        """An ELF's embedded search path cannot opt into the execution tool's libc."""
        execution_libc = next((tools.resolve() / "usr/lib").glob("*/libc.so.6"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "library.so"
            with tarfile.open(library) as archive:
                member = next(item for item in archive if item.isfile() and item.name.endswith(".so.1"))
                binary.write_bytes(archive.extractfile(member).read())
            subprocess.run([str(patchelf.resolve()), "--set-rpath", str(execution_libc.parent), str(binary)], check=True)
            payload = root / "rpath.tar"
            data = binary.read_bytes()
            with tarfile.open(payload, "w") as archive:
                member = tarfile.TarInfo("usr/lib/libmetadata-fixture.so.1")
                member.size, member.mode = len(data), 0o755
                archive.addfile(member, io.BytesIO(data))
            result = self.generate(root, control.resolve(), payload)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stderr, r"(?:cannot|couldn't) find library libc\.so\.6")

    def test_standard_helpers_resolve_script_service_and_debconf_dependencies(self):
        """Existing Debian helper inputs supply their dependencies without handwritten versions."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "metadata-scripts",
                "Depends: ${python3:Depends}, ${perl:Depends}, ${misc:Depends}, ${misc:Pre-Depends}", architecture="all")
            payload = root / "scripts.tar"
            with tarfile.open(payload, "w") as archive:
                for name, data in {
                    "usr/bin/metadata-python": b"#!/usr/bin/python3\nprint('fixture')\n",
                    "usr/bin/metadata-perl": b"#!/usr/bin/perl\nprint qq(fixture\\n);\n",
                    "etc/init.d/custom-service": b"#!/bin/sh\nexit 0\n",
                }.items():
                    member = tarfile.TarInfo(name)
                    member.size, member.mode = len(data), 0o755
                    archive.addfile(member, io.BytesIO(data))
            templates = root / "templates"
            templates.write_text("Template: metadata-scripts/enabled\nType: boolean\nDefault: false\nDescription: Example question\n")
            result = self.generate(root, source_control, payload, package="metadata-scripts",
                additional=["--debian-file", "metadata-scripts.templates=" + str(templates)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fields = json.loads((root / "metadata.json").read_text())["packages"]["metadata-scripts"]["control_fields"]
            for name in ("python3", "perl", "debconf", "init-system-helpers"):
                self.assertIn(name, fields["Depends"])
            self.assertNotIn("${", fields["Depends"])

    def test_time64_provides_comes_from_debhelper(self):
        """Let Debian's architecture-aware helper determine the compatibility Provides field."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_control = self.control(root, "libmetadatat64",
                "Depends: ${shlibs:Depends}, ${misc:Pre-Depends}\nProvides: ${t64:Provides}")
            result = self.generate(root, source_control, library, package="libmetadatat64",
                additional=["--apt-package", str(libc_data.resolve()) + "=" + str(libc_control.resolve())])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fields = json.loads((root / "metadata.json").read_text())["packages"]["libmetadatat64"]["control_fields"]
            self.assertEqual(fields["Provides"], "libmetadata (= 2.0)")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
