"""Preserve subtree extraction and opt into complete SWIG tree validation.

Archive fixtures specify bytes and links independently. Legacy extraction permits
subtrees and unmatched prefixes; complete trees explicitly require swig.swg.
All extraction modes keep files inside the declared tree.
"""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from extract_templates import extract


class TemplatesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "templates.tar"
        self.output = self.root / "templates"

    def archive_members(self, members):
        with tarfile.open(self.archive, "w") as archive:
            for name, contents, kind in members:
                info = tarfile.TarInfo(name)
                info.mode = 0o644
                if kind == "file":
                    info.size = len(contents)
                    archive.addfile(info, io.BytesIO(contents))
                else:
                    info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                    info.linkname = contents
                    archive.addfile(info)

    def test_templates_and_in_tree_links_preserve_bytes(self):
        self.archive_members([("usr/share/swig/swig.swg", b"%module example\n", "file"),
                              ("usr/share/swig/python/alias.swg", "../swig.swg", "symlink"),
                              ("usr/share/swig/copy.swg", "usr/share/swig/swig.swg", "hardlink")])
        extract([self.archive], "usr/share/swig", self.output, require_entry_point=True)
        for name in ("swig.swg", "python/alias.swg", "copy.swg"):
            self.assertEqual((self.output / name).read_bytes(), b"%module example\n")
        self.assertFalse((self.output / "usr").exists())

    def test_missing_prefix_cannot_succeed_with_an_empty_tree(self):
        self.archive_members([("another/swig.swg", b"template", "file")])
        with self.assertRaisesRegex(ValueError, "lacks required swig.swg"):
            extract([self.archive], "usr/share/swig", self.output, require_entry_point=True)

    def test_template_subdirectory_does_not_require_entry_point(self):
        self.archive_members([("usr/share/swig/python/pyoperators.swg", b"python templates", "file")])
        extract([self.archive], "usr/share/swig/python", self.output)
        self.assertEqual((self.output / "pyoperators.swg").read_bytes(), b"python templates")
        with self.assertRaisesRegex(ValueError, "lacks required swig.swg"):
            extract([self.archive], "usr/share/swig/python", self.root / "strict",
                    require_entry_point=True)

    def test_legacy_unmatched_prefix_produces_an_empty_tree(self):
        self.archive_members([("another/swig.swg", b"template", "file")])
        extract([self.archive], "usr/share/swig", self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_traversal_in_archive_is_rejected(self):
        self.archive_members([("../outside", b"escape", "file")])
        with self.assertRaisesRegex(ValueError, "unsafe SWIG archive path"):
            extract([self.archive], "usr/share/swig", self.output)
        self.assertFalse((self.root / "outside").exists())

    def test_link_cannot_escape_the_template_subtree(self):
        self.archive_members([("usr/share/swig/swig.swg", "../../../outside", "symlink")])
        with self.assertRaisesRegex(ValueError, "escapes declared subtree"):
            extract([self.archive], "usr/share/swig", self.output)

    def test_prefix_must_be_an_explicit_relative_subtree(self):
        for prefix in ("", ".", "/usr/share/swig", "../swig"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                extract([], prefix, self.output)


if __name__ == "__main__":
    unittest.main()
