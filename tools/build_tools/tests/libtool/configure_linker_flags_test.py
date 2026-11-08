"""Reproduce PI's pre-configure E2BIG, then link using the shared transport.

The parent shell constructs an oversized environment with builtins, just like
rules_foreign_cc. The declared Python cannot start until the sourced helper
compacts LDFLAGS. Real C/C++ links from a nested directory check that the existing
compiler launcher still preserves search priority and quoted paths with spaces.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def checked(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, timeout=120, **kwargs)
    assert result.returncode == 0, result.stderr[-16000:]
    return result


def child():
    expected = json.loads(Path("expected.json").read_text())
    assert os.environ["CPPFLAGS"] == expected["cppflags"]
    assert os.environ["LDFLAGS"] == "@" + str(Path.cwd() / ".sonic-linker-arguments")
    vector = Path(".sonic-linker-arguments").read_text().splitlines()
    assert vector == ["sonic-linker-arguments-v1", *expected["arguments"]]
    nested = Path.cwd() / "recursive" / "libtool"
    nested.mkdir(parents=True)
    source = Path.cwd() / "main.c"
    source.write_text("int library_choice(void); int main(void) { return library_choice() != 11; }\n")
    languages = []
    for language, variable, flags in (("c", "CC", "CFLAGS"), ("c++", "CXX", "CXXFLAGS")):
        compiler = shlex.split(os.environ[variable])
        options = shlex.split(os.environ.get(flags, "")) + shlex.split(os.environ["CPPFLAGS"])
        obj = Path.cwd() / (language + ".o")
        output = Path.cwd() / (language + "-linked")
        checked(compiler + options + ["-x", language, "-c", str(source), "-o", str(obj)])
        # Give both archives the language's symbol spelling. Their search order
        # determines whether the linked program exits zero (11) or nonzero (22).
        for directory, value in zip(expected["library_directories"], (11, 22)):
            library_source = Path(directory) / "choice.c"
            library_source.write_text("int library_choice(void) { return " + str(value) + "; }\n")
            library_object = Path(directory) / "choice.o"
            checked(compiler + options + ["-x", language, "-c", str(library_source), "-o", str(library_object)])
            checked(shlex.split(os.environ["AR"]) + ["rcs", str(Path(directory) / "libchoice.a"), str(library_object)])
        checked(compiler + options + [str(obj), os.environ["LDFLAGS"], "-o", str(output)], cwd=nested)
        checked([str(output)], cwd=nested)
        languages.append(language)
    Path("child.json").write_text(json.dumps({"cppflags_preserved": True, "nested_links": languages}))


def parent():
    adapter = Path(os.environ["BAZEL_CONFIGURE_LINKER_FLAGS"]).absolute()
    environment = Path(os.environ["BAZEL_CONFIGURE_ENV"]).absolute()
    python = str(Path(os.environ["BAZEL_FOREIGN_TOOLS"]) / "bin/python3")
    script = Path(__file__).absolute()
    directory = Path.cwd() / "preexec-linker-test" / ("shared-prefix-" + "x" * 100)
    directory.mkdir(parents=True)
    libraries = [directory / "first library with spaces", directory / "second-library"]
    for library in libraries:
        library.mkdir()
    arguments = ["-L" + str(library) for library in libraries]
    arguments += ["-L" + str(directory / ("missing-" + str(index))) for index in range(571)]
    arguments += ["-Wl,--start-group", "-lchoice", "-Wl,--end-group", "-Wl,-z,now"]
    # Match at least PI's observed 160,581 value bytes / 573 search directories,
    # independently of the length of the Bazel execution root used by this test.
    padding = max(0, 160581 - len(shlex.join(arguments).encode()))
    arguments[572] += "x" * padding
    raw = directory / "ldflags.txt"
    raw.write_text(shlex.join(arguments) + "\n")
    expected = {"arguments": arguments, "cppflags": os.environ.get("CPPFLAGS", ""),
                "library_directories": [str(path) for path in libraries]}
    (directory / "expected.json").write_text(json.dumps(expected))
    env = dict(os.environ, PWD=str(directory))
    # configure_environment.sh takes the selected real compiler, not an already
    # wrapped command. Reuse the same declared drivers as the outer fixture.
    for name in ("CC", "CXX"):
        compiler = shlex.split(env[name])
        assert Path(compiler[0]).name == ".sonic-compiler-argv"
        # The adapter repeats joined -B options from CFLAGS/CXXFLAGS in the
        # command for Libtool. They remain in those flags for the new adapter.
        assert all(option.startswith("-B") for option in compiler[2:]), compiler
        env[name] = compiler[1]
    read_flags = 'IFS= read -r LDFLAGS < "$1"; export LDFLAGS; shift; '
    baseline = subprocess.run(["/bin/bash", "-c", read_flags + 'exec "$@"', "preexec",
                               str(raw), "/bin/sh", str(environment), python, str(script), "--child"],
                              cwd=directory, env=env, capture_output=True, text=True)
    assert baseline.returncode != 0 and "Argument list too long" in baseline.stderr, baseline.stderr[-2000:]
    checked(["/bin/bash", "-c", read_flags + '. "$@"', "preexec", str(raw), str(adapter),
             python, "/bin/sh", str(environment), python, str(script), "--child"], cwd=directory, env=env)
    report = json.loads((directory / "child.json").read_text())
    report.update(ldflags_bytes=len(raw.read_bytes()) - 1, library_search_arguments=573,
                  original_failed_e2big=True)

    # Serialization retains empty, duplicated, quoted, backslash and ordinary
    # response-file operands. A newline would corrupt the line-vector protocol.
    tokens = ["", "literal with spaces", "quote'and\"double", "back\\slash", "-Lrepeat", "-Lrepeat", "@ordinary.rsp"]
    command = ["/bin/bash", "-c", 'export LDFLAGS="$1"; shift; . "$@"', "boundaries"]
    checked(command + [shlex.join(tokens), str(adapter), python, python, "-c", "pass"], cwd=directory, env=env)
    assert (directory / ".sonic-linker-arguments").read_text().splitlines() == ["sonic-linker-arguments-v1", *tokens]
    report["argument_boundaries_preserved"] = True
    for newline in ("\n", "\r"):
        rejected = subprocess.run(command + [shlex.join(["bad" + newline + "argument"]), str(adapter),
                                             python, python, "-c", "raise AssertionError('command must not run')"],
                                  cwd=directory, env=env, text=True, capture_output=True)
        assert rejected.returncode != 0 and "Linker arguments cannot contain line breaks" in rejected.stderr
        assert "command must not run" not in rejected.stderr
    report["line_breaks_rejected"] = True
    Path("configure-linker-flags-validation.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--child"]:
        child()
    else:
        parent()
