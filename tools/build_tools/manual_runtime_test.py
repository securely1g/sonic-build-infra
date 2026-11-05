"""Public documentation tools preserve includes, extensions, resources and versions.

Contract: tool-recipes.md's Sphinx/Texinfo recipes generate document content using
only declared execution tools and data. Host search paths must not supply them.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPHINX, MAKEINFO = sys.argv[1:]
sys.argv[1:] = []


class ManualToolsTest(unittest.TestCase):
    def test_sphinx_includes_autodoc_theme_and_static_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "_static").mkdir(parents=True)
            (source / "conf.py").write_text(
                'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n'
                'project="Declared manuals"\nversion=release="1.2.3"\n'
                'extensions=["sphinx.ext.autodoc"]\nhtml_theme="alabaster"\n'
                'html_static_path=["_static"]\nexclude_patterns=["details.rst"]\n')
            (source / "index.rst").write_text(
                'Declared manuals\n================\n\nRelease |release|.\n\n'
                '.. include:: details.rst\n\n.. autofunction:: example.answer\n')
            (source / "details.rst").write_text('Included source contract marker.\n')
            (source / "example.py").write_text(
                'def answer():\n    """Function documentation sentinel."""\n    return 53\n')
            (source / "_static/custom.css").write_text('/* declared stylesheet */\n')
            output = root / "html"
            env = dict(os.environ, PATH="", PYTHONPATH="/absent/host-python",
                       PYTHONHOME="/absent/host-python", PYTHONUSERBASE="/absent/user-python",
                       PYTHONOPTIMIZE="2", PYTHONPLATLIBDIR="/absent/platform-python")
            subprocess.run([str(Path(SPHINX).absolute()), "-W", "-b", "html", str(source), str(output)],
                           env=env, check=True)
            html = (output / "index.html").read_text()
            for value in ("Included source contract marker", "Function documentation sentinel", "1.2.3"):
                self.assertIn(value, html)
            self.assertEqual((output / "_static/custom.css").read_text(), '/* declared stylesheet */\n')

    def test_texinfo_include_and_version_with_empty_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manual.texi").write_text(
                '\\input texinfo\n@setfilename example.info\n@settitle Declared manuals\n'
                '@set VERSION 1.2.3\n@node Top\n@top Declared manuals\n'
                '@include details.texi\n@bye\n')
            (root / "details.texi").write_text(
                '@chapter Contract\nIncluded Texinfo contract marker @value{VERSION}.\n')
            output = root / "manual.info"
            env = dict(os.environ, PATH="", PERL5LIB="/absent/host-perl", PERL5OPT="-MAbsentHostModule")
            subprocess.run([str(Path(MAKEINFO).absolute()), "--no-split", "-I", str(root),
                            "-o", str(output), str(root / "manual.texi")], env=env, check=True)
            self.assertIn("Included Texinfo contract marker 1.2.3", output.read_text())


if __name__ == "__main__":
    unittest.main()
