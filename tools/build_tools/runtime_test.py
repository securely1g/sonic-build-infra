"""Exercise installed-tool behavior from test runfiles and sandboxed actions."""

import argparse
import json
from pathlib import Path
import platform
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("aspell")
    parser.add_argument("doxygen")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    # Preserve executable symlinks: the adjacent runfiles tree is the tool's
    # execution contract, including when invoked from another working directory.
    aspell, doxygen = [str(Path(path).absolute()) for path in (args.aspell, args.doxygen)]
    with tempfile.TemporaryDirectory(prefix="build-tools-test-") as temporary:
        root = Path(temporary)
        env = {"HOME": temporary, "PATH": "", "LANG": "C", "LC_ALL": "C"}

        def run(command, text=None):
            result = subprocess.run(command, input=text, cwd=root, env=env, text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(f"{command} exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
            return result.stdout

        aspell_version = run([aspell, "--version"]).strip()
        assert "0.60.8.1" in aspell_version, aspell_version
        (root / "personal.pws").write_text("personal_ws-1.1 en 1\nsoniccustomword\n")
        spelling = run([aspell, "-l", "en", "-a", "-p", "./personal.pws"], "switch definess soniccustomword\n")
        assert "& definess " in spelling, spelling
        assert spelling.splitlines().count("*") == 2, spelling

        doxygen_version = run([doxygen, "-v"]).strip()
        (root / "probe.h").write_text("/** @file probe.h */\n/** A sample declaration. */\nvoid probe(void);\n")
        (root / "Doxyfile").write_text("\n".join([
            "PROJECT_NAME = Probe", "INPUT = probe.h", "OUTPUT_DIRECTORY = output",
            "GENERATE_HTML = NO", "GENERATE_LATEX = NO", "GENERATE_XML = YES",
            "QUIET = YES", "HAVE_DOT = NO", "WARN_AS_ERROR = YES", "",
        ]))
        run([doxygen, "Doxyfile"])
        assert "probe.h" in (root / "output/xml/index.xml").read_text()
        result = {"architecture": platform.machine(), "aspell_version": aspell_version,
                  "doxygen_version": doxygen_version, "english_dictionary": True,
                  "relative_personal_dictionary": True, "misspelling_detected": True,
                  "doxygen_xml_generated": True, "empty_path": True}
        if args.report:
            args.report.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
