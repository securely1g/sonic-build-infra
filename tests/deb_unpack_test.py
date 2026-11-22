"""Exercise dpkg's own tar reader on a package with long debug paths."""
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def main(package, content, basename):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(["dpkg", "--root=" + directory, "--log=" + str(root / "dpkg.log"),
                        "--no-triggers",
                        "--unpack", str(package.resolve())], check=True)
        debug = root / "usr/lib/debug"
        payload = debug / basename
        assert payload.read_bytes() == content.read_bytes()
        assert stat.S_IMODE(payload.stat().st_mode) == 0o640
        assert (debug / "by-name").readlink() == Path(basename)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
