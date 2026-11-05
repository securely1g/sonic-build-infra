"""The program linked via quoted ordered ld response inputs must return success."""
import json
from pathlib import Path
import sys
import unittest

class ResponseFixtureTest(unittest.TestCase):
    def test_linked_program(self):
        self.assertEqual(json.loads((Path(sys.argv[1]) / "configuration/result.json").read_text())["program_exit_code"], 0)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ResponseFixtureTest)
    result = unittest.TextTestRunner().run(suite)
    raise SystemExit(not result.wasSuccessful())
