"""Configure must compile against a declared relocatable pkg-config header."""
from pathlib import Path
import sys
import unittest

output = Path(sys.argv.pop())


class RelocatablePkgconfigTest(unittest.TestCase):
    def test_configure_compiled_the_declared_header(self):
        self.assertEqual((output / 'configuration/probe-pc.o').read_bytes()[:4], b'\x7fELF')


if __name__ == '__main__':
    unittest.main()
