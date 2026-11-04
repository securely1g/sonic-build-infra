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


report = {"include_cases": [], "long_commands": [], "long_library_commands": [], "library_cases": [],
          "system_include_cases": [], "long_system_commands": [], "header_warning_cases": [],
          "private_linker_commands": []}
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

    # Distroless CcInfo exports quote/system roots, rather than ordinary -I.
    # Preserve each search class, its order and precedence over forwarded flags.
    system_cases = [
        (["-I" + str(second), "-iquote", str(first)], '"choice.h"', "FIRST"),
        (["-isystem" + str(second), "-iquote" + str(first)], '"choice.h"', "FIRST"),
        (["-isystem", str(first), "-isystem" + str(second)], "<choice.h>", "FIRST"),
        (["-isystem" + str(second), "-isystem", str(first)], "<choice.h>", "SECOND"),
        (["-Xpreprocessor", "-isystem" + str(forwarded), "-isystem", str(first)], "<choice.h>", "FIRST"),
        (["-Wp,-iquote," + str(forwarded), "-iquote", str(first), "-isystem" + str(second)], '"choice.h"', "FIRST"),
    ]
    for arguments, header, expected_value in system_cases:
        command = flags + ["-E", "-P", "-x", language, "-", *arguments]
        source_text = "#include " + header + "\n"
        before = checked(original(compiler) + command, source=source_text).stdout.strip()
        after = checked(compiler + command, source=source_text).stdout.strip()
        assert before == after == expected_value, (arguments, before, after)
        report["system_include_cases"].append({"language": language, "selected": after})

    warning_header = first / "warning.h"
    warning_header.write_text("static inline int warn(int unused) { return 1; }\n")
    for kind in ("-isystem", "-iquote"):
        command = flags + [kind, str(first), "-Werror=unused-parameter", "-fsyntax-only", "-x", language, "-"]
        source_text = '#include "warning.h"\nint main(void) { return warn(1) - 1; }\n'
        before = run(original(compiler) + command, source=source_text)
        after = run(compiler + command, source=source_text)
        expected_success = kind == "-isystem"
        assert (before.returncode == 0) == (after.returncode == 0) == expected_success, (kind, before.stderr, after.stderr)
        if not expected_success:
            assert "unused" in before.stderr and "unused" in after.stderr
        report["header_warning_cases"].append({"language": language, "kind": kind, "success": expected_success})

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

    # PI's current Debian SDK has 132 -iquote and 272 -isystem arguments plus
    # its library closure. None is individually too long, but GCC collects them
    # into one oversized environment string. Exercise both joined/split forms.
    system_flags = []
    for index in range(500):
        option = "-iquote" if index % 3 == 0 else "-isystem"
        directory = prefix + "/headers-" + str(index)
        system_flags.extend([option, directory] if index % 2 else [option + directory])
    arguments = flags + system_flags + [library] * (90000 // len(library)) + [
        "-x", language, str(source), "-o", str(output)] + shlex.split(os.environ.get("LDFLAGS", ""))
    before = run(original(compiler) + arguments)
    assert before.returncode != 0 and "Argument list too long" in before.stderr, before.stderr[-16000:]
    checked(compiler + arguments)
    checked([str(output)])
    report["long_system_commands"].append({"language": language,
        "include_argument_bytes": sum(map(len, system_flags)), "original_failed_e2big": True,
        "adapted_compiled_and_ran": True})

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

    # Owners can keep oversized LDFLAGS out of the environment, while the same
    # driver still shortens paths for each recursive Make/Libtool directory.
    linker_response = Path(compiler[0]).parent / ".sonic-linker-arguments"
    linker_response.write_text("sonic-linker-arguments-v1\n" + "\n".join(libraries) + "\n")
    private_arguments = flags + ["@" + str(linker_response), str(obj), "-o", str(output)]
    checked(compiler + private_arguments, cwd=long_cwd)
    checked([str(output)], cwd=long_cwd)
    diagnostic = checked(compiler + ["-###", *private_arguments], cwd=long_cwd)
    collected = [len(line.encode()) for line in diagnostic.stderr.splitlines()
                 if line.startswith("COLLECT_GCC_OPTIONS=")]
    assert collected and max(collected) < 131072, collected
    report["private_linker_commands"].append({"language": language,
        "search_argument_bytes": sum(map(len, libraries)),
        "max_collect_gcc_options_bytes": max(collected), "linked_and_ran": True})

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

# Paired operands and response files are opaque; they must not become headers.
arguments = ["-o", "-isystem-output", "-Xlinker", "-iquote-linker", "-iquote", "quote one",
             "-isystemsystem-one", "-iquotequote-two", "-isystem", "system two", "-include", "-isystem-input"]
expected = ["-Xpreprocessor", "-iquote", "-Xpreprocessor", "quote one", "-Xpreprocessor", "-isystemsystem-one",
            "-Xpreprocessor", "-iquotequote-two", "-Xpreprocessor", "-isystem", "-Xpreprocessor", "system two",
            *arguments[:4], *arguments[-2:]]
assert checked([launcher, str(capture), *arguments]).stdout.splitlines() == expected
for missing in ("-I", "-iquote", "-isystem"):
    result = run([launcher, str(capture), missing])
    assert result.returncode == 2 and "Missing compiler argument after " + missing in result.stderr
response_arguments = ["-iquotequote", "-isystem", "system", "@untouched-response-file"]
assert checked([launcher, str(capture), *response_arguments]).stdout.splitlines() == response_arguments
report["system_operands_and_response_files_preserved"] = True

# Only the exact private sibling vector is interpreted. Literal empty, space,
# backslash and option-like operands retain their positions; other @files stay
# opaque even after private-vector expansion.
private_response = Path(launcher).parent / ".sonic-linker-arguments"
private_tokens = ["-Wl,-z,now", "", "literal with space", "back\\slash", "-Xlinker", "-Loperand"]
private_response.write_text("sonic-linker-arguments-v1\n" + "\n".join(private_tokens) + "\n")
private_argument = "@" + str(private_response)
assert checked([launcher, str(capture), "before", private_argument, "after"], cwd=nested).stdout.splitlines() == [
    "before", *private_tokens, "after"]
assert checked([launcher, str(capture), "-L" + absolute, private_argument,
                "@untouched-response-file"], cwd=nested).stdout.splitlines() == [
    "-L" + absolute, *private_tokens, "@untouched-response-file"]
private_response.write_text("not-the-private-format\n-Lunexpected\n")
rejected = run([launcher, str(capture), private_argument], cwd=nested)
assert rejected.returncode == 2 and "Invalid private linker argument response" in rejected.stderr
assert rejected.stdout == ""
private_response.unlink()
report["private_linker_argument_boundaries_preserved"] = True

# Keep the real Autoconf preprocessor/compiler probes and recursive Libtool
# builds under the same large native-header closure after the small checks.
# Each environment value stays below Linux's per-string exec limit.
configure_flags = []
while sum(len(arg) + 1 for arg in configure_flags) < 75000:
    index = len(configure_flags)
    option = "-iquote" if index % 3 == 0 else "-isystem"
    configure_flags.append(option + prefix + "/configure-" + str(index))
os.environ["CPPFLAGS"] = os.environ.get("CPPFLAGS", "") + " " + " ".join(configure_flags)
os.environ["LDFLAGS"] = os.environ.get("LDFLAGS", "") + " " + " ".join([library] * (90000 // len(library)))
assert len(os.environ["CPPFLAGS"].encode()) < 131000
assert len(os.environ["LDFLAGS"].encode()) < 131000
report["autoconf_native_header_arguments"] = len(configure_flags)
Path("compiler-argv-validation.json").write_text(json.dumps(report, indent=2) + "\n")
os.execv(sys.argv[1], sys.argv[1:])
