"""Reject source stanzas and mismatched metadata before packaging a binary."""

import unittest

from check_control import check_control


CONTROL = """Package: sonic-example
Version: 1:2.3-4
Architecture: arm64
Maintainer: SONiC maintainers <sonic-dev@lists.sonicfoundation.dev>
Depends: libc6 (>= 2.41), libstdc++6
Multi-Arch: same
Breaks: sonic-example-old (<< 2.0)
Description: Example runtime
 A continuation with fields retained from the source package.
"""


class ControlTest(unittest.TestCase):
    def validate(self, text):
        check_control(text, "sonic-example", "1:2.3-4", "arm64")

    def test_accepts_complete_control(self):
        self.validate(CONTROL)

    def test_rejects_wrong_identity(self):
        for old, new in (("sonic-example\n", "other\n"), ("1:2.3-4", "2.3"), ("arm64", "amd64")):
            with self.subTest(old=old), self.assertRaises(ValueError):
                self.validate(CONTROL.replace(old, new, 1))

    def test_rejects_unresolved_dependencies(self):
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.validate(CONTROL.replace("libc6 (>= 2.41)", "${shlibs:Depends}"))

    def test_rejects_source_control(self):
        with self.assertRaisesRegex(ValueError, "one binary"):
            self.validate("Source: sonic-example\n\n" + CONTROL)

    def test_rejects_duplicate_fields(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.validate(CONTROL + "architecture: amd64\n")

    def test_requires_description_and_maintainer(self):
        for field in ("Maintainer", "Description"):
            lines = [line for line in CONTROL.splitlines() if not line.startswith(field + ":") and not line.startswith(" ")]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "required"):
                self.validate("\n".join(lines))


if __name__ == "__main__":
    unittest.main()
