"""Check actual Python/Go wrappers for configured and default SWIG features.

The enabled interfaces require a bare define and a name=value define. Checking
both branches catches ignored definitions and accidental changes to defaults.
"""

from pathlib import Path
import sys


for filename in sys.argv[1:]:
    content = Path(filename).read_text()
    enabled = "/enabled/" in filename
    expected = "enabled_feature" if enabled else "disabled_feature"
    excluded = "disabled_feature" if enabled else "enabled_feature"
    assert expected in content, (filename, expected)
    assert excluded not in content, (filename, excluded)
