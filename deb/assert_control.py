"""Compare Debian control fields semantically, independent of field order."""
from pathlib import Path
import sys

from debian.deb822 import Deb822


def read(path):
    entries = list(Deb822.iter_paragraphs(Path(path).read_text().splitlines()))
    if len(entries) != 1:
        raise ValueError('expected one binary control paragraph: ' + str(path))
    return dict(entries[0])


if __name__ == '__main__':
    actual, expected = (read(path) for path in sys.argv[1:])
    if actual != expected:
        raise SystemExit('Debian control fields differ:\nactual=' + repr(actual) + '\nexpected=' + repr(expected))
