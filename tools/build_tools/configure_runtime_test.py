"""Exercise the installed configure tools and Perl's relocated core/XS modules."""

import argparse
import json
from pathlib import Path
import platform
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    for tool in ("make", "m4", "pkgconf", "perl", "bison", "flex", "autoconf", "autoheader", "autom4te"):
        parser.add_argument(tool)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    tools = {name: str(Path(getattr(args, name)).absolute()) for name in ("make", "m4", "pkgconf", "perl", "bison", "flex", "autoconf", "autoheader", "autom4te")}
    with tempfile.TemporaryDirectory(prefix="configure-tools-") as temporary:
        root = Path(temporary)
        env = {"PATH": "", "HOME": temporary, "LC_ALL": "C", "LANG": "C"}

        def run(tool, arguments, text=None, extra_env=None):
            result = subprocess.run([tools[tool], *arguments], input=text, cwd=root,
                                    env=env | (extra_env or {}), text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(f"{tool} {arguments}: {result.returncode}\n{result.stdout}\n{result.stderr}")
            return result.stdout

        versions = {name: run(name, ["--version"]).strip() for name in tools}
        (root / "configure.ac").write_text("AC_INIT([probe], [1.0])\nAC_CONFIG_HEADERS([config.h])\nAC_DEFINE([VALUE], [42], [Probe value])\nAC_OUTPUT\n")
        run("autoconf", [])
        run("autoheader", [])
        assert "#undef VALUE" in (root / "config.h.in").read_text()
        assert "probe" in (root / "configure").read_text()
        (root / "parser.y").write_text("%%\ninput: 'a';\n%%\n")
        run("bison", ["--output=parser.c", "parser.y"])
        assert "yyparse" in (root / "parser.c").read_text()
        (root / "scanner.l").write_text("%option noyywrap\n%%\n[a-z]+ ;\n%%\n")
        run("flex", ["--outfile=scanner.c", "scanner.l"])
        assert "yylex" in (root / "scanner.c").read_text()
        # Recursion must use the relocated wrapper, not the raw binary whose
        # interpreter path points at /lib in the host filesystem.
        (root / "Makefile").write_text('all:\n\t@$(MAKE) --no-print-directory nested\nnested:\n\t@printf "recursive make works\\n"\n')
        assert "recursive make works" in run("make", ["--no-print-directory"])
        assert run("m4", [], "define(`SONIC', `declared m4')SONIC\n").strip() == "declared m4"

        (root / "probe.pc").write_text('prefix=/declared\nName: probe\nDescription: Declared package\nVersion: 1.2.3\nLibs: -L${prefix}/lib -lprobe\n')
        assert run("pkgconf", ["--modversion", "probe"], extra_env={"PKG_CONFIG_LIBDIR": temporary}).strip() == "1.2.3"
        unknown = subprocess.run([tools["pkgconf"], "--exists", "libzstd"], cwd=root, env=env)
        assert unknown.returncode != 0, "pkgconf must not fall back to the host package database"

        # Digest::SHA, Cwd and Data::Dumper load native Perl extension modules.
        # Every core dependency must come from the declared runtime, while an
        # explicit -I remains available for caller-owned source modules.
        (root / "Consumer.pm").write_text('package Consumer; sub value { "consumer" } 1;\n')
        assert run("perl", ["-MIPC::Cmd", "-e", "1"]) == ""
        script = r'''
use Config; use Cwd; use Digest::SHA qw(sha256_hex); use JSON::PP; use Data::Dumper;
use Symbol qw(gensym); use IO::Handle;
die "incorrect Symbol import" unless ref(gensym()) eq "GLOB";
die "incorrect IO import" unless ref(IO::Handle->new()) eq "IO::Handle";
use Consumer;
print encode_json({loaded => \%INC, inc => \@INC, digest => sha256_hex("sonic"),
                   consumer => Consumer::value(), cwd => getcwd()});
'''
        assert run("perl", ["-e", 'system($^X, "-e", "print qq(recursive perl works)") == 0 or die;']) == "recursive perl works"
        report = json.loads(run("perl", ["-I", temporary, "-e", script]))
        assert report["consumer"] == "consumer" and report["cwd"] == temporary, report
        assert report["digest"] == "7da6940c6ef99d18fbf4bf9d83b3b62ce5e1de7889d4aa8a815ad23c754aa327", report
        for module, path in report["loaded"].items():
            if module == "Consumer.pm":
                assert path == str(root / "Consumer.pm"), path
            else:
                assert "/configure_runtime_" in path, (module, path)
        assert not any(path.startswith(("/usr/", "/etc/perl")) for path in report["inc"]), report
        result = {"architecture": platform.machine(), "versions": versions,
                  "empty_path": True, "parser_and_scanner_generation": True, "recursive_make": True, "declared_pkgconfig": True,
                  "perl_core_and_xs_modules": report["loaded"], "consumer_perl_module": True}
        if args.report:
            args.report.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
