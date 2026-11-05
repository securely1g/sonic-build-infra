"""Check published API descriptions and a real Graphviz call graph."""

from pathlib import Path
import sys
import unittest

DOCUMENTATION = Path(sys.argv.pop(1))


class DocumentationTest(unittest.TestCase):
    def test_html_and_call_graph(self):
        self.assertTrue((DOCUMENTATION / "index.html").is_file())
        pages = "\n".join(path.read_text() for path in DOCUMENTATION.glob("*.html"))
        self.assertIn("Double an integer", pages)
        self.assertIn("Compute the answer", pages)
        graphs = "\n".join(path.read_text() for path in DOCUMENTATION.glob("*.svg"))
        self.assertIn("twice", graphs)
        self.assertIn("answer", graphs)


if __name__ == "__main__":
    unittest.main()
