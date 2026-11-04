"""Check ordered positional files, location argv and unchanged literal arguments."""
from pathlib import Path
import sys

first, second, *arguments = sys.argv[1:]
short, long = Path(first), Path(second)
assert short.name == "foo" and short.read_text() == "short source file\n", first
assert long.name == "foobar" and long.read_text() == "long source file\n", second
spaced = long.parent / "data file's.txt"
assert spaced.read_text() == "space and apostrophe source file\n"
expected = [
    "--long=" + str(long),
    "--literal=testing/argv/foo",
    "--spaced=" + str(spaced),
    "--text=a value with 'quotes'; untouched",
]
assert arguments == expected, ("argv contract", arguments, expected)
print("PASS: ordered files, prefix names, literal preservation, spaces and quotes")
