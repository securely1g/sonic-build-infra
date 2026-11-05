"""The declared configure generator must produce the input's error contract."""
import pathlib
import sys
import unittest

output = pathlib.Path(sys.argv.pop())


class DeclaredConfigureToolsTest(unittest.TestCase):
    def test_error_table_is_generated_by_the_declared_exec_tool(self):
        header = (output / 'configuration/probe.h').read_text()
        source = (output / 'configuration/probe.c').read_text()
        self.assertRegex(header, r'#define\s+E_TASK\s+')
        self.assertIn('declared configure generator works', source)


if __name__ == '__main__':
    unittest.main()
