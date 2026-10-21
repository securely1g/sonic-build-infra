"""Exercise the real static bootstrap over a pre-existing package file alias."""

from pathlib import Path
import subprocess
import sys
import tempfile

from prepare_runtime import write_launcher


def main():
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="runtime launcher-") as temporary:
        root = Path(temporary)
        (root / "bin").mkdir()
        original = root / "bin/tool-v1"
        original.write_bytes(b"original package payload\n")
        (root / "bin/tool").symlink_to(original.name)
        write_launcher(root, "tool", '#!/bin/sh\nprintf "%s\\n" "$1"\nexit 31\n', executable)
        assert original.read_bytes() == b"original package payload\n", "overwrote an aliased package file"
        result = subprocess.run([str(root / "bin/tool"), "argument with spaces"],
                                env={"PATH": ""}, capture_output=True, text=True)
        assert result.returncode == 31 and result.stdout == "argument with spaces\n", result
    print("Existing package alias preserved; declared static launcher uses its own sidecar")


if __name__ == "__main__":
    main()
