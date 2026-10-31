"""Check package metadata and archive boundaries without creating DEBs."""

import io
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

from prepare_rootfs import add_target_packages, merge_target_payload, configure_xml_sax, initialize_root, package_metadata, preserve_empty_directories, remove_x11_alias, validate_links
from prepare_runtime import extract_payload


def write_tar(path, records):
    with tarfile.open(path, "w") as archive:
        for name, data in records.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


class RootfsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "root"
        initialize_root(self.root)

    def test_merged_usr_preserves_absolute_path_tools(self):
        payload = self.directory / "data.tar"
        write_tar(payload, {"./bin/tool": b"tool", "./usr/lib/library": b"library"})
        extract_payload(self.root, payload)
        self.assertEqual((self.root / "usr/bin/tool").read_bytes(), b"tool")
        self.assertEqual((self.root / "lib/library").read_bytes(), b"library")

    def test_x11_recursive_alias_is_removed_without_losing_commands(self):
        commands = self.root / "usr/bin"
        commands.mkdir(exist_ok=True)
        (commands / "dot").write_text("declared Graphviz")
        (commands / "X11").symlink_to(".")

        remove_x11_alias(self.root)

        self.assertFalse((commands / "X11").is_symlink())
        self.assertEqual((commands / "dot").read_text(), "declared Graphviz")
        validate_links(self.root)

    def test_x11_nonrecursive_directory_is_preserved(self):
        directory = self.root / "usr/bin/X11"
        directory.mkdir()
        (directory / "tool").write_text("declared tool")
        remove_x11_alias(self.root)
        self.assertEqual((directory / "tool").read_text(), "declared tool")

    def test_xml_sax_configuration_resolves_packaged_symlink(self):
        module = self.root / "usr/share/perl5/XML/SAX.pm"
        module.parent.mkdir(parents=True)
        module.touch()
        directory = module.parent / "SAX"
        directory.mkdir()
        (directory / "PurePerl.pm").touch()
        link = directory / "ParserDetails.ini"
        link.symlink_to("../../../../../etc/perl/XML/SAX/ParserDetails.ini")

        configure_xml_sax(self.root)

        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.root / "etc/perl/XML/SAX/ParserDetails.ini")
        self.assertIn("[XML::SAX::PurePerl]\nhttp://xml.org/sax/features/namespaces = 1\n", link.read_text())
        validate_links(self.root)

    def test_xml_sax_configuration_is_absent_without_module(self):
        configure_xml_sax(self.root)
        self.assertFalse((self.root / "etc/perl/XML/SAX").exists())

    def test_xml_sax_configuration_preserves_declared_config(self):
        module = self.root / "usr/share/perl5/XML/SAX.pm"
        module.parent.mkdir(parents=True)
        module.touch()
        config = self.root / "etc/perl/XML/SAX/ParserDetails.ini"
        config.parent.mkdir(parents=True)
        content = "# Declared package configuration\n[XML::SAX::PurePerl]\n"
        config.write_text(content)

        configure_xml_sax(self.root)

        self.assertEqual(config.read_text(), content)

    def test_file_only_cache_roundtrip_preserves_empty_directory_links(self):
        # Model the cache downloader creating file/symlink parents but omitting
        # DirectoryNodes that contain no files. The real merged-/usr aliases
        # must still resolve, as must an alias to a nested empty directory.
        (self.root / "var/cache/example/empty").mkdir(parents=True)
        (self.root / "empty-cache").symlink_to("var/cache/example/empty")
        (self.root / "usr/bin/tool").write_bytes(b"tool")

        def roundtrip(destination):
            destination.mkdir()
            for directory, directories, files in os.walk(self.root, followlinks=False):
                for name in directories + files:
                    source = Path(directory) / name
                    target = destination / source.relative_to(self.root)
                    if source.is_symlink():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.symlink_to(source.readlink())
                    elif source.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)

        unpreserved = self.directory / "unpreserved"
        roundtrip(unpreserved)
        with self.assertRaisesRegex(ValueError, "unresolved runtime"):
            validate_links(unpreserved)

        preserve_empty_directories(self.root)
        preserved = self.directory / "preserved"
        roundtrip(preserved)
        validate_links(preserved)
        original_directories = {
            Path(directory).relative_to(self.root)
            for directory, _, _ in os.walk(self.root, followlinks=False)
        }
        cached_directories = {
            Path(directory).relative_to(preserved)
            for directory, _, _ in os.walk(preserved, followlinks=False)
        }
        self.assertEqual(cached_directories, original_directories)
        self.assertEqual((preserved / "usr/bin/tool").read_bytes(), b"tool")
        self.assertFalse((preserved / "usr/bin/.bazel-keep-directory").exists())
        for alias in ("lib32", "libx32", "empty-cache"):
            self.assertTrue((preserved / alias).is_dir())
            self.assertEqual((preserved / alias / ".bazel-keep-directory").read_bytes(), b"")

    def test_library_metadata_preserves_package_ownership(self):
        payload = self.directory / "data.tar"
        control = self.directory / "control.tar"
        write_tar(payload, {"./usr/lib/libsample.so.1": b"library"})
        write_tar(control, {
            "./control": b"Package: libsample\nVersion: 1.2\nArchitecture: amd64\nMulti-Arch: same\n",
            "./shlibs": b"libsample 1 libsample (>= 1.2)\n",
            "./postinst": b"must not execute",
        })
        package_metadata(self.root, payload, control)
        info = self.root / "var/lib/dpkg/info"
        self.assertEqual((info / "format").read_text(), "1\n")
        self.assertEqual((info / "libsample:amd64.list").read_text(), "/usr/lib/libsample.so.1\n")
        self.assertEqual((info / "libsample:amd64.shlibs").read_text(), "libsample 1 libsample (>= 1.2)\n")
        self.assertFalse((info / "libsample:amd64.postinst").exists())

    def test_cross_target_metadata_preserves_native_status(self):
        status = self.root / "var/lib/dpkg/status"
        native = "Package: libsample\nVersion: 1.2\nArchitecture: amd64\nStatus: install ok installed\n"
        status.write_text(native)
        packages = []
        for architecture, triple in (("arm64", "aarch64-linux-gnu"), ("armhf", "arm-linux-gnueabihf")):
            payload = self.directory / (architecture + ".tar")
            control = self.directory / (architecture + "-control.tar")
            write_tar(payload, {"usr/lib/" + triple + "/libsample.so.1": architecture.encode()})
            write_tar(control, {"control": ("Package: libsample\nVersion: 1.2\nArchitecture: " + architecture + "\nMulti-Arch: same\n").encode(), "shlibs": b"libsample 1 libsample (>= 1.2)\n"})
            packages.append((payload, control))
        add_target_packages(self.root, packages, ["arm64", "armhf"])
        self.assertTrue(status.read_text().startswith(native))
        self.assertEqual(status.read_text().count("Package: libsample"), 3)
        for architecture in ("arm64", "armhf"):
            self.assertIn("Architecture: " + architecture, status.read_text())
            self.assertTrue((self.root / ("var/lib/dpkg/info/libsample:" + architecture + ".shlibs")).is_file())
        self.assertEqual((self.root / "var/lib/dpkg/arch").read_text(), "amd64\narm64\narmhf\n")

    def test_architecture_independent_status_is_not_duplicated(self):
        status = self.root / "var/lib/dpkg/status"
        status.write_text("Package: shared-headers\nVersion: 1.2\nArchitecture: all\nStatus: install ok installed\n")
        payload = self.directory / "target.tar"
        control = self.directory / "target-control.tar"
        write_tar(payload, {"usr/include/shared.h": b"header"})
        write_tar(control, {"control": b"Package: shared-headers\nVersion: 1.2\nArchitecture: all\n"})
        add_target_packages(self.root, [(payload, control), (payload, control)], ["arm64", "armhf"])
        self.assertEqual(status.read_text().count("Package: shared-headers"), 1)

    def test_target_cannot_replace_native_executable(self):
        (self.root / "usr/bin/tool").write_bytes(b"native")
        payload = self.directory / "target.tar"
        write_tar(payload, {"usr/bin/tool": b"foreign"})
        with self.assertRaisesRegex(ValueError, "replaces an execution input"):
            merge_target_payload(self.root, payload)
        self.assertEqual((self.root / "usr/bin/tool").read_bytes(), b"native")

    def test_matching_multiarch_headers_are_shared(self):
        (self.root / "usr/include").mkdir()
        (self.root / "usr/include/shared.h").write_bytes(b"shared")
        payload = self.directory / "target.tar"
        write_tar(payload, {"usr/include/shared.h": b"shared", "usr/lib/arm-linux-gnueabihf/target.so": b"target"})
        merge_target_payload(self.root, payload)
        self.assertEqual((self.root / "usr/include/shared.h").read_bytes(), b"shared")
        self.assertEqual((self.root / "usr/lib/arm-linux-gnueabihf/target.so").read_bytes(), b"target")

    def test_target_overlays_cannot_replace_another_targets_header(self):
        first = self.directory / "arm64.tar"
        second = self.directory / "armhf.tar"
        write_tar(first, {"usr/include/shared.h": b"first target"})
        write_tar(second, {"usr/include/shared.h": b"second target"})
        merge_target_payload(self.root, first)
        with self.assertRaisesRegex(ValueError, "replaces an execution input"):
            merge_target_payload(self.root, second)
        self.assertEqual((self.root / "usr/include/shared.h").read_bytes(), b"first target")

    def test_archive_path_escape_is_rejected(self):
        payload = self.directory / "bad.tar"
        write_tar(payload, {"../outside": b"bad"})
        with self.assertRaises(ValueError):
            extract_payload(self.root, payload)
        self.assertFalse((self.directory / "outside").exists())

    def test_symlink_escape_is_rejected(self):
        (self.root / "usr/bin/unsafe").symlink_to("../../../../outside")
        with self.assertRaisesRegex(ValueError, "escapes output"):
            validate_links(self.root)

    def test_deferred_hardlink_parent_replacement_is_rejected(self):
        (self.root / "inside").mkdir()
        outside = self.directory / "outside"
        outside.mkdir()
        payload = self.directory / "bad-hardlink.tar"
        with tarfile.open(payload, "w") as archive:
            source = tarfile.TarInfo("safe")
            source.size = 4
            archive.addfile(source, io.BytesIO(b"safe"))
            parent = tarfile.TarInfo("a")
            parent.type = tarfile.SYMTYPE
            parent.linkname = "inside"
            archive.addfile(parent)
            link = tarfile.TarInfo("a/escaped")
            link.type = tarfile.LNKTYPE
            link.linkname = "safe"
            archive.addfile(link)
            parent.linkname = "../outside"
            archive.addfile(parent)
        with self.assertRaises(ValueError):
            extract_payload(self.root, payload)
        self.assertFalse((outside / "escaped").exists())

    def test_unresolved_executable_is_rejected(self):
        (self.root / "usr/bin/missing").symlink_to("absent")
        with self.assertRaisesRegex(ValueError, "unresolved runtime"):
            validate_links(self.root)

    def test_sudo_systemd_null_mask_is_omitted_without_device_nodes(self):
        parent = self.root / "usr/lib/systemd/system"
        parent.mkdir(parents=True)
        mask = parent / "sudo.service"
        mask.symlink_to("../../../../dev/null")
        validate_links(self.root)
        self.assertFalse(mask.is_symlink())
        self.assertFalse((self.root / "dev/null").exists())

    def test_null_link_outside_systemd_units_is_rejected(self):
        (self.root / "usr/bin/missing").symlink_to("../../dev/null")
        with self.assertRaisesRegex(ValueError, "unresolved runtime"):
            validate_links(self.root)

    def test_systemd_link_to_another_missing_path_is_rejected(self):
        parent = self.root / "usr/lib/systemd/system"
        parent.mkdir(parents=True)
        (parent / "sudo.service").symlink_to("../../../../dev/missing")
        with self.assertRaisesRegex(ValueError, "unresolved runtime"):
            validate_links(self.root)

    def test_another_systemd_null_mask_is_rejected(self):
        parent = self.root / "usr/lib/systemd/system"
        parent.mkdir(parents=True)
        (parent / "unrelated.service").symlink_to("../../../../dev/null")
        with self.assertRaisesRegex(ValueError, "unresolved runtime"):
            validate_links(self.root)

    def test_unresolved_manpage_does_not_require_an_unrelated_package(self):
        directory = self.root / "usr/share/man/man1"
        directory.mkdir(parents=True)
        missing = directory / "optional.1"
        missing.symlink_to("missing.1")
        validate_links(self.root)
        self.assertFalse(missing.is_symlink())


if __name__ == "__main__":
    unittest.main()
