"""Verify real owner/helper control-file handoff through the public rule.

Debhelper 13.24.2 compat 10 and dh_installinit's documented -r policy are the
independent oracle. The fixture has original script bodies and two /etc files.
No maintainer script, account action, or service is executed by this test.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile
import unittest

preserved, preserved_tree, restart, restart_tree, default, default_tree, payload = map(Path, sys.argv[1:8])
authored = dict(zip(["preinst", "postinst", "postrm"], map(Path, sys.argv[8:11])))
del sys.argv[1:]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RuntimeControlFilesRuleTest(unittest.TestCase):
    def package(self, metadata):
        return json.loads(metadata.read_text())["packages"]["service-fixture"]

    def control(self, metadata, tree, name):
        entry = self.package(metadata)["control_files"][name]
        path = tree / entry["path"]
        self.assertEqual(entry["sha256"], digest(path))
        return path.read_text(), entry

    def test_owner_logic_and_real_helper_output_are_preserved(self):
        record = self.package(preserved)
        self.assertTrue({"control", "preinst", "postinst", "prerm", "postrm", "conffiles"} <= set(record["control_files"]))
        for script, original in authored.items():
            result, entry = self.control(preserved, preserved_tree, script)
            prefix, suffix = original.read_text().split("#DEBHELPER#")
            self.assertTrue(result.startswith(prefix))
            self.assertTrue(result.endswith(suffix))
            self.assertNotIn("#DEBHELPER#", result)
            self.assertEqual(entry["mode"], "0755")
        postinst, _ = self.control(preserved, preserved_tree, "postinst")
        self.assertIn("deb-systemd-helper", postinst)
        self.assertIn("service-fixture.service", postinst)
        _, prerm = self.control(preserved, preserved_tree, "prerm")
        self.assertEqual(prerm["mode"], "0755")
        conffiles, entry = self.control(preserved, preserved_tree, "conffiles")
        self.assertEqual(set(conffiles.splitlines()), {
            "/etc/init.d/service-fixture", "/etc/service-fixture/settings.conf",
        })
        self.assertEqual(entry["mode"], "0644")

    def test_original_no_restart_on_upgrade_policy_differs_from_restart_default(self):
        # Upstream postinst-init invokes start; postinst-init-restart chooses
        # restart on upgrade ($2 set). These are external shipped-script semantics.
        no_restart, _ = self.control(preserved, preserved_tree, "postinst")
        restart_default, _ = self.control(restart, restart_tree, "postinst")
        self.assertRegex(no_restart, r"invoke-rc.d [^\n]*service-fixture start")
        self.assertNotIn("_dh_action=restart", no_restart)
        self.assertIn("_dh_action=restart", restart_default)
        self.assertRegex(restart_default, r"invoke-rc.d [^\n]*service-fixture \$_dh_action")

    def test_default_api_and_all_payload_bytes_remain_unchanged(self):
        self.assertNotIn("control_files", self.package(default))
        self.assertFalse((default_tree / "packages/service-fixture/DEBIAN/postinst").exists())
        for metadata, tree in [(preserved, preserved_tree), (restart, restart_tree), (default, default_tree)]:
            self.assertEqual(self.package(metadata)["payload_sha256"], digest(payload))
            with tarfile.open(payload) as archive:
                for member in archive:
                    if member.isfile():
                        path = tree / "packages/service-fixture" / member.name.removeprefix("./")
                        self.assertEqual(path.read_bytes(), archive.extractfile(member).read())


if __name__ == "__main__":
    unittest.main()
