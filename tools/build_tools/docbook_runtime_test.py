"""Compare SONiC's real WPA manuals with Debian's unchanged man backend."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    runtime = Path(sys.argv[1]).resolve()
    # Keep the public executable's test-runfiles context; following its input
    # symlink would detach the wrapper from the declared interpreter/runtime.
    tool = Path(sys.argv[2]).absolute()
    documents = [Path(value).resolve() for value in sys.argv[3:]]
    assert len(documents) == 5
    with tempfile.TemporaryDirectory(prefix="docbook-tools-") as temporary:
        work = Path(temporary)
        environment = {"PATH": "", "HOME": temporary, "LC_ALL": "C", "SOURCE_DATE_EPOCH": "1598266417", "TMPDIR": temporary}

        def run(command, directory, extra=None, succeeds=True):
            result = subprocess.run(list(map(str, command)), cwd=directory, env=environment | (extra or {}), text=True, capture_output=True)
            if succeeds:
                assert result.returncode == 0, (command, result.stdout, result.stderr)
            else:
                assert result.returncode != 0, (command, result.stdout)
            return result

        report = {}
        for document in documents:
            output = work / document.stem
            baseline = work / (document.stem + "-baseline")
            output.mkdir()
            baseline.mkdir()
            run([tool, document], output)
            manifest = json.loads((runtime / "toolchain.json").read_text())
            # Execute the original package backend with the same declared
            # parser, Perl modules and catalogs; no host Jade is involved.
            run([runtime / "bin/sh", runtime / "usr/share/docbook-utils/backends/man"], baseline, {
                "PATH": str(runtime / "bin"),
                "SGML_FILE": str(document),
                "SGML_CATALOG_FILES": ":".join(str(runtime / name) for name in manifest["sgml_catalogs"]),
                "SGML_SEARCH_PATH": str(runtime / "usr/share/sgml"),
                "PERL5LIB": str(runtime / "usr/share/perl5/sgmlspl-specs"),
            })
            filename = document.stem + (".5" if document.stem.endswith(".conf") else ".8")
            result = (output / filename).read_bytes()
            assert result == (baseline / filename).read_bytes(), filename
            assert b".TH " in result and b"24 August 2020" in result, filename
            report[filename] = hashlib.sha256(result).hexdigest()

        # The prepared runtime is a relocatable action input, including its
        # SGML catalogs and Perl module search path. Preserve argument spaces.
        relocated = work / "relocated runtime"
        shutil.copytree(runtime, relocated)
        relocated_output = work / "relocated-output"
        relocated_output.mkdir()
        run([relocated / "bin/docbook2man", documents[1]], relocated_output)
        assert hashlib.sha256((relocated_output / "wpa_cli.8").read_bytes()).hexdigest() == report["wpa_cli.8"]

        invalid = work / "invalid.sgml"
        invalid.write_text("<!DOCTYPE refentry PUBLIC \"-//OASIS//DTD DocBook V4.2//EN\"><refentry><unknown>broken\n")
        run([tool, invalid], work, succeeds=False)
        run([tool, work / "missing.sgml"], work, succeeds=False)
        run([tool, "--unsupported"], work, succeeds=False)
        run([tool, documents[0]], work, {"SOURCE_DATE_EPOCH": ""}, succeeds=False)
        print(json.dumps({"manuals": report, "empty_path": True, "original_backend_bytes": True, "relocated_runtime": True, "invalid_inputs_rejected": True}, indent=2))


if __name__ == "__main__":
    main()
