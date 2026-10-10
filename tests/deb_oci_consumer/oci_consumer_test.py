"""Verify real rules_oci outputs built from existing-package importer TARs.

This test only reads declared artifacts. It never creates a DEB, extracts files
onto the host, runs maintainer scripts, loads a Docker daemon, or starts a guest.
"""

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import unittest


INPUTS = sys.argv[1:]
del sys.argv[1:]


def digest(contents):
    return hashlib.sha256(contents).hexdigest()


def path_name(name):
    """Compare semantically identical './usr' and 'usr' TAR path spellings."""
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/")


class OciConsumerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (
            cls.image,
            cls.image_repeat,
            cls.base,
            cls.base_repeat,
            cls.overlay,
            cls.overlay_repeat,
            cls.base_manifest,
            cls.overlay_manifest,
            cls.fixture_hashes,
        ) = map(Path, INPUTS[:-1])
        cls.architecture = INPUTS[-1]

    def blob(self, descriptor):
        algorithm, checksum = descriptor["digest"].split(":", 1)
        self.assertEqual(algorithm, "sha256")
        self.assertRegex(checksum, r"^[0-9a-f]{64}$")
        contents = (self.image / "blobs" / algorithm / checksum).read_bytes()
        self.assertEqual(len(contents), descriptor["size"])
        self.assertEqual(digest(contents), checksum)
        return contents

    def image_metadata(self):
        self.assertEqual(
            json.loads((self.image / "oci-layout").read_text()),
            {"imageLayoutVersion": "1.0.0"},
        )
        index = json.loads((self.image / "index.json").read_text())
        self.assertEqual(index["schemaVersion"], 2)
        self.assertEqual(len(index["manifests"]), 1)
        descriptor = index["manifests"][0]
        self.assertEqual(descriptor["mediaType"], "application/vnd.oci.image.manifest.v1+json")
        manifest = json.loads(self.blob(descriptor))
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["config"]["mediaType"], "application/vnd.oci.image.config.v1+json")
        config = json.loads(self.blob(manifest["config"]))
        return manifest, config

    def test_image_consumes_public_default_tars_in_order(self):
        manifest, config = self.image_metadata()
        self.assertEqual(config["os"], "linux")
        self.assertEqual(config["architecture"], self.architecture)
        self.assertEqual(config["created"], "1970-01-01T00:00:00Z")
        self.assertEqual(config["rootfs"]["type"], "layers")
        expected = [self.base.read_bytes(), self.overlay.read_bytes()]
        self.assertEqual(len(manifest["layers"]), 2)
        self.assertEqual(config["rootfs"]["diff_ids"], ["sha256:" + digest(data) for data in expected])
        for descriptor, data in zip(manifest["layers"], expected):
            self.assertEqual(descriptor["mediaType"], "application/vnd.oci.image.layer.v1.tar")
            self.assertEqual(self.blob(descriptor), data)

    def test_visible_content_and_metadata_survive_image_assembly(self):
        manifest, _ = self.image_metadata()
        visible = {}
        for descriptor, expected_value in zip(manifest["layers"], [b"gz", b"raw"]):
            with tarfile.open(fileobj=io.BytesIO(self.blob(descriptor))) as archive:
                members = {path_name(member.name): member for member in archive.getmembers()}
                self.assertEqual(set(members), {
                    "usr/share/example",
                    "usr/share/example/value",
                    "usr/share/example/symlink",
                    "usr/share/example/hardlink",
                })
                value = members["usr/share/example/value"]
                self.assertEqual(archive.extractfile(value).read(), expected_value)
                self.assertEqual(members["usr/share/example"].mode, 0o750)
                self.assertEqual(value.mode, 0o640)
                self.assertTrue(members["usr/share/example/symlink"].issym())
                self.assertEqual(members["usr/share/example/symlink"].linkname, "value")
                self.assertTrue(members["usr/share/example/hardlink"].islnk())
                self.assertEqual(path_name(members["usr/share/example/hardlink"].linkname), "usr/share/example/value")
                for member in members.values():
                    self.assertEqual((member.uid, member.gid), (123, 456))
                    self.assertEqual((member.uname, member.gname), ("sonic-test", "sonic-test-group"))
                    self.assertEqual(member.mtime, 1700000000)
                    self.assertEqual(member.pax_headers["SCHILY.xattr.user.example"], "preserved")
                # Model the regular-file overwrite in image layer order without
                # extracting ownership-bearing archives onto the test machine.
                for name, member in members.items():
                    visible[name] = archive.extractfile(member).read() if member.isfile() else member.linkname
        self.assertEqual(visible["usr/share/example/value"], b"raw")
        self.assertNotIn("postinst", visible)
        self.assertNotIn("control", visible)

    def test_image_layers_match_verified_package_receipts(self):
        hashes = json.loads(self.fixture_hashes.read_text())
        for payload, receipt_path, expected_packages in [
            (self.base, self.base_manifest, ["example-gz"]),
            (self.overlay, self.overlay_manifest, ["example-xz", "example-bz2", "example-raw"]),
        ]:
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["architecture"], self.architecture)
            self.assertEqual(receipt["payload"]["sha256"], digest(payload.read_bytes()))
            self.assertEqual([record["package"] for record in receipt["packages"]], expected_packages)
            for record in receipt["packages"]:
                self.assertEqual(record["source_sha256"], hashes[record["source_deb"]])
                self.assertEqual(record["architecture"], "all")
                self.assertEqual(record["control_files"]["postinst"], digest(b"#!/bin/sh\nexit 99\n"))

    def test_independent_actions_are_deterministic(self):
        self.assertEqual(self.base.read_bytes(), self.base_repeat.read_bytes())
        self.assertEqual(self.overlay.read_bytes(), self.overlay_repeat.read_bytes())

        def layout_files(root):
            return {str(path.relative_to(root)): digest(path.read_bytes())
                    for path in root.rglob("*") if path.is_file()}

        # Includes index/config/layer descriptors and the actual blob bytes.
        self.assertEqual(layout_files(self.image), layout_files(self.image_repeat))


if __name__ == "__main__":
    unittest.main()
