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


def run(command, *, source=None):
    return subprocess.run(command, input=source, text=True, capture_output=True,
                          env=dict(os.environ, LC_ALL="C"), timeout=120)


def checked(command, *, source=None):
    result = run(command, source=source)
    assert result.returncode == 0, result.stderr[-16000:]
    return result


def original(command):
    return command[1:] if Path(command[0]).name == ".sonic-compiler-argv" else command


report = {"include_cases": [], "long_commands": []}
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
Path("compiler-argv-validation.json").write_text(json.dumps(report, indent=2) + "\n")
os.execv(sys.argv[1], sys.argv[1:])
