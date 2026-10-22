"""Exercise the actual configured GCC drivers before Autoconf/Libtool runs."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


root = Path.cwd() / "compiler-argv-test"
root.mkdir(exist_ok=True)
first, second, forwarded = [root / name for name in ("native first", "native-second", "forwarded")]
for directory, value in ((first, "FIRST"), (second, "SECOND"), (forwarded, "FORWARDED")):
    directory.mkdir(exist_ok=True)
    (directory / "choice.h").write_text(value + "\n")


def run(command, *, source=None, cwd=None):
    return subprocess.run(command, input=source, text=True, capture_output=True,
                          env=dict(os.environ, LC_ALL="C", PWD=str(cwd or Path.cwd())),
                          cwd=cwd, timeout=120)


def checked(command, *, source=None, cwd=None):
    result = run(command, source=source, cwd=cwd)
    assert result.returncode == 0, result.stderr[-16000:]
    return result


def original(command):
    return command[1:] if Path(command[0]).name == ".sonic-compiler-argv" else command


report = {"include_cases": [], "long_commands": [], "long_library_commands": [], "library_cases": []}
for language, compiler_variable, flags_variable in (("c", "CC", "CFLAGS"), ("c++", "CXX", "CXXFLAGS")):
    compiler = shlex.split(os.environ[compiler_variable])
    flags = shlex.split(os.environ.get(flags_variable, "")) + shlex.split(os.environ.get("CPPFLAGS", ""))
    cases = [
        ["-I" + str(first), "-I", str(second)],
        ["-Xpreprocessor", "-I" + str(forwarded), "-I", str(first), "-I" + str(second)],
        ["-Wp,-I" + str(forwarded), "-I" + str(second), "-I", str(first)],
        ["-isystem", str(first), "-I" + str(second)],
    ]
    for arguments in cases:
        command = flags + ["-E", "-P", "-x", language, "-", *arguments]
        before = checked(original(compiler) + command, source="#include <choice.h>\n").stdout.strip()
        after = checked(compiler + command, source="#include <choice.h>\n").stdout.strip()
        assert before == after, (arguments, before, after)
        assert after in ("FIRST", "SECOND"), after
        report["include_cases"].append({"language": language, "selected": after})

    # Keep response-file groups in GCC's control: a hidden native include must
    # retain its precedence over an existing explicitly forwarded include.
    response = root / ("includes-" + language + ".rsp")
    response.write_text('-I"' + str(second) + '"\n')
    response_args = flags + ["-E", "-P", "-x", language, "-", "-Xpreprocessor", "-I" + str(forwarded), "@" + str(response)]
    before = checked(original(compiler) + response_args, source="#include <choice.h>\n").stdout.strip()
    after = checked(compiler + response_args, source="#include <choice.h>\n").stdout.strip()
    assert before == after == "SECOND", (before, after)

    # These nonexistent search directories are valid GCC inputs. Their total
    # sizes reproduce a large CcInfo closure without staging unrelated headers.
    prefix = str(root / ("long-path-" + "x" * 180))
    include = "-I" + prefix
    library = "-L" + prefix
    long_flags = [include] * (70000 // len(include)) + [library] * (90000 // len(library))
    source = root / ("main-" + language + ".c")
    source.write_text("int main(void) { return 0; }\n")
    output = root / ("main-" + language)
    arguments = flags + long_flags + ["-x", language, str(source), "-o", str(output)] + shlex.split(os.environ.get("LDFLAGS", ""))
    before = run(original(compiler) + arguments)
    assert before.returncode != 0 and "Argument list too long" in before.stderr, before.stderr[-16000:]
    checked(compiler + arguments)
    checked([str(output)])
    report["long_commands"].append({"language": language, "search_argument_bytes": sum(map(len, long_flags)),
                                    "original_failed_e2big": True, "adapted_compiled_and_ran": True})

    # A library-only closure still overflows COLLECT_GCC_OPTIONS after native
    # includes have been handled. Use distinct directories under a long shared
    # prefix, as produced by rules_foreign_cc for Debian library dependencies.
    long_cwd = root
    for index in range(4):
        long_cwd /= "shared-execution-root-" + str(index) + "-" + "x" * 100
    long_cwd.mkdir(parents=True, exist_ok=True)
    libraries = ["-L" + str(long_cwd / ("missing-library-" + str(index))) for index in range(400)]
    # Precompile first so the failing invocation must spawn collect2, rather
    # than reproducing only the earlier cc1/cc1plus include-path failure.
    obj = root / ("link-only-" + language + ".o")
    checked(compiler + flags + ["-x", language, "-c", str(source), "-o", str(obj)])
    arguments = flags + libraries + [str(obj), "-o", str(output)]
    before = run(original(compiler) + arguments, cwd=long_cwd)
    assert before.returncode != 0 and "Argument list too long" in before.stderr, before.stderr[-16000:]
    assert "collect2" in before.stderr, before.stderr[-16000:]
    checked(compiler + arguments, cwd=long_cwd)
    checked([str(output)], cwd=long_cwd)
    report["long_library_commands"].append({"language": language, "search_argument_bytes": sum(map(len, libraries)),
                                            "original_failed_e2big": True, "adapted_linked_and_ran": True})

# Prove that native -L precedence over linker-forwarded paths remains intact.
# Each archive exports the same symbol with a different value, so choosing a
# different library changes the executable's exit status.
library_dirs = [root / name for name in ("library first, with space", "library-second", "library-forwarded")]
cc = shlex.split(os.environ["CC"])
cflags = shlex.split(os.environ.get("CFLAGS", ""))
for directory, value in zip(library_dirs, (11, 22, 33)):
    directory.mkdir(exist_ok=True)
    source = directory / "choice.c"
    source.write_text("int library_choice(void) { return " + str(value) + "; }\n")
    obj = directory / "choice.o"
    checked(cc + cflags + ["-c", str(source), "-o", str(obj)])
    checked(shlex.split(os.environ["AR"]) + ["rcs", str(directory / "libchoice.a"), str(obj)])
library_link = root / "library-link"
library_link.symlink_to(library_dirs[0], target_is_directory=True)
nested = root / "nested" / "deeper"
nested.mkdir(parents=True, exist_ok=True)
logical = root / "logical-cwd"
logical.symlink_to(nested, target_is_directory=True)
library_source = root / "library-main.c"
library_source.write_text("int library_choice(void); int main(void) { return library_choice() != EXPECTED; }\n")
cases = [
    (["-L" + str(library_dirs[0]), "-L", str(library_dirs[1])], 11),
    (["-Wl,-L," + str(library_dirs[2]), "-L" + str(library_dirs[0]), "-L" + str(library_dirs[1])], 11),
    (["-L" + str(library_dirs[1]), "-L" + str(library_dirs[0])], 22),
    (["-L", str(library_link), "-L" + str(library_dirs[1])], 11),
]
for cwd in (nested, logical):
    for index, (arguments, expected_value) in enumerate(cases):
        output = root / "library-main"
        command = cflags + arguments + ["-DEXPECTED=" + str(expected_value), str(library_source), "-lchoice", "-o", str(output)]
        checked(original(cc) + command, cwd=cwd)
        checked([str(output)], cwd=cwd)
        checked(cc + command, cwd=cwd)
        checked([str(output)], cwd=cwd)
        report["library_cases"].append({"cwd": cwd.name, "case": index, "selected": expected_value})

# The driver must not reinterpret an output/linker operand as an include flag.
# Inspect the exact argument sequence without invoking another compiler.
capture = root / "capture"
capture.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
capture.chmod(0o755)
launcher = shlex.split(os.environ["CC"])[0]
arguments = ["-Xlinker", "-I-linker-input", "-L", "first", "-Lsecond", "-o", "-I-output",
             "-Xpreprocessor", "-I-forwarded", "-I", "native one", "-Inative-two", "-lchoice"]
captured = checked([launcher, str(capture), *arguments]).stdout.splitlines()
expected = ["-Xpreprocessor", "-Inative one", "-Xpreprocessor", "-Inative-two", *arguments[:9], "-lchoice"]
assert captured == expected, (captured, expected)
report["linker_and_other_operands_preserved"] = True

# Check exact spelling, duplicate/order preservation, nonexistent paths, and
# operands that happen to look like library-search flags. Keep '..' and response
# files untouched instead of simplifying across a possible symlink.
absolute = str(root / "missing library,dir")
parent_path = str(library_link) + "/../different"
relative = os.path.relpath(absolute, nested)
arguments = ["-L" + absolute, "-L", absolute, "-Lrelative", "-L=/sysroot", "-L" + parent_path,
             "-Xlinker", "-L" + absolute, "-o", "-Loutput"]
expected = ["-L" + relative, "-L", relative, *arguments[3:]]
for cwd in (nested, logical):
    captured = checked([launcher, str(capture), *arguments], cwd=cwd).stdout.splitlines()
    assert captured == expected, (captured, expected)
response_arguments = ["-L" + absolute, "@untouched-response-file"]
captured = checked([launcher, str(capture), *response_arguments], cwd=nested).stdout.splitlines()
assert captured == response_arguments, (captured, response_arguments)
report["library_path_arguments_preserved"] = True
Path("compiler-argv-validation.json").write_text(json.dumps(report, indent=2) + "\n")
os.execv(sys.argv[1], sys.argv[1:])
