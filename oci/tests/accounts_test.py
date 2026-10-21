"""Keep inherited account bytes and reject conflicting service identities."""

import json
from pathlib import Path
import tempfile
import unittest

from accounts import build, merge
from fixture import archive, layout


class AccountTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.passwd = b"root:x:0:0:root:/root:/bin/bash\n_apt:x:42:65534::/nonexistent:/usr/sbin/nologin\n"
        self.group = b"root:x:0:\nnogroup:x:65534:\n"
        self.original = archive(self.root / "base.tar", {"etc/passwd": self.passwd, "etc/group": self.group})
        self.users = archive(self.root / "users.tar", {"etc/passwd": b"redis:x:100:101::/var/lib/redis:/usr/sbin/nologin\n"})
        self.groups = archive(self.root / "groups.tar", {"etc/group": b"redis:x:101:\n"})

    def run_build(self, layers=None, architecture="amd64"):
        base = layout(self.root / "image", layers or [self.original], architecture)
        return build(base, self.users, self.groups, architecture)

    def test_keeps_all_inherited_lines_and_adds_only_service_accounts(self):
        for architecture in ("amd64", "arm64"):
            with self.subTest(architecture=architecture):
                files, receipt = self.run_build(architecture=architecture)
                self.assertEqual(files["etc/passwd"], self.passwd + b"redis:x:100:101::/var/lib/redis:/usr/sbin/nologin\n")
                self.assertEqual(files["etc/group"], self.group + b"redis:x:101:\n")
                self.assertEqual(receipt["architecture"], architecture)
                self.assertEqual(receipt["users"], ["redis"])
                self.assertEqual(receipt["files"]["etc/passwd"]["mode"], 0o644)

    def test_identical_account_is_idempotent_and_preserves_bytes(self):
        self.assertEqual(merge(self.passwd, self.passwd, "passwd"), self.passwd)

    def test_different_definition_or_same_id_is_rejected(self):
        for row in (b"root:x:0:0:changed:/root:/bin/bash\n", b"service:x:0:0::/:/bin/false\n"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                merge(self.passwd, row, "passwd")

    def test_new_users_cannot_share_numeric_identity(self):
        with self.assertRaisesRegex(ValueError, "already belongs"):
            merge(self.passwd, b"one:x:100:0::/:/bin/false\ntwo:x:100:0::/:/bin/false\n", "passwd")

    def test_primary_group_must_exist(self):
        self.groups = archive(self.root / "groups.tar", {"etc/group": b""})
        with self.assertRaisesRegex(ValueError, "primary group"):
            self.run_build()

    def test_group_members_must_exist(self):
        self.groups = archive(self.root / "groups.tar", {"etc/group": b"redis:x:101:missing\n"})
        with self.assertRaisesRegex(ValueError, "absent user"):
            self.run_build()

    def test_rejects_changed_base_account_mode(self):
        self.original = archive(self.root / "base.tar", {"etc/passwd": (self.passwd, 0o600), "etc/group": self.group})
        with self.assertRaisesRegex(ValueError, "ownership or mode"):
            self.run_build()

    def test_whiteout_cannot_resurrect_lower_accounts(self):
        for name in ("etc/.wh.passwd", "etc/.wh..wh..opq", ".wh.etc"):
            with self.subTest(name=name):
                removed = archive(self.root / "removed.tar", {name: b""})
                with self.assertRaisesRegex(ValueError, "lacks passwd or group"):
                    self.run_build([self.original, removed])

    def test_same_layer_replacement_survives_whiteout(self):
        replaced = archive(self.root / "replaced.tar", {"etc/.wh.passwd": b"", "etc/passwd": self.passwd})
        self.assertEqual(self.run_build([self.original, replaced])[0]["etc/passwd"].splitlines()[0], self.passwd.splitlines()[0])

    def test_hash_mismatch_is_rejected(self):
        base = layout(self.root / "image", [self.original])
        index = json.loads((base / "index.json").read_text())
        digest = index["manifests"][0]["digest"].split(":")[1]
        (base / "blobs/sha256" / digest).write_text("changed")
        with self.assertRaisesRegex(ValueError, "digest or size"):
            build(base, self.users, self.groups, "amd64")


if __name__ == "__main__":
    unittest.main()
