"""Execute an isolated configure probe and retain its declared input identity."""
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def absolute_arguments(arguments, root):
    result = []
    path_next = False
    for argument in arguments:
        if path_next:
            result.append(str(root / argument) if not argument.startswith("/") else argument)
            path_next = False
        elif argument in ("-I", "-L", "-isystem", "-iquote", "-idirafter", "--sysroot", "-B"):
            result.append(argument)
            path_next = True
        elif argument.startswith(("-Wl,-rpath,", "-Wl,-rpath=", "-Wl,-rpath-link,", "-Wl,-rpath-link=")):
            prefix = next(p for p in ("-Wl,-rpath,", "-Wl,-rpath=", "-Wl,-rpath-link,", "-Wl,-rpath-link=") if argument.startswith(p))
            paths = argument[len(prefix):].split(":")
            result.append(prefix + ":".join(str(root / p) if p and not p.startswith(("/", "$")) else p for p in paths))
        elif argument.startswith("-Wl,"):
            parts = argument.split(",")
            for index in range(1, len(parts)):
                flag, separator, value = parts[index].partition("=")
                candidate = value if separator else flag
                if candidate and not candidate.startswith(("-", "/", "$")) and (root / candidate).exists():
                    parts[index] = (flag + separator if separator else "") + str(root / candidate)
            result.append(",".join(parts))
        elif argument.startswith(("--sysroot=", "-isystem", "-iquote", "-I", "-L", "-B")):
            prefix = next(p for p in ("--sysroot=", "-isystem", "-iquote", "-I", "-L", "-B") if argument.startswith(p))
            value = argument[len(prefix):]
            result.append(prefix + (str(root / value) if value and not value.startswith("/") else value))
        elif not argument.startswith("-") and (root / argument).exists():
            result.append(str(root / argument))
        else:
            result.append(argument)
    return result


def relative_arguments(arguments, root, cwd):
    """Shorten declared input paths without changing runtime loader paths."""
    root = os.path.normpath(str(root))

    def shorten(value):
        if value == root or value.startswith(root + os.sep):
            return os.path.relpath(value, cwd)
        return value

    inputs = ("-I", "-L", "-isystem", "-iquote", "-idirafter", "--sysroot", "-B", "-include", "-imacros")
    linker_inputs = ("-L", "--library-path", "-T", "--script", "--version-script", "--dynamic-list", "--retain-symbols-file", "-rpath-link", "--rpath-link")
    runtime = ("-rpath", "--rpath", "-R", "--dynamic-linker", "-dynamic-linker", "-soname", "--soname", "-h")
    result = []
    mode = None
    for argument in arguments:
        if argument == "-Xlinker":
            result.append(argument)
            continue
        if argument.startswith("-Wl,"):
            parts = []
            for part in argument[4:].split(","):
                if mode:
                    parts.append(":".join(shorten(p) for p in part.split(":")) if mode == "input" else part)
                    mode = None
                elif part in linker_inputs or part in runtime:
                    parts.append(part)
                    mode = "input" if part in linker_inputs else "runtime"
                elif "=" in part:
                    key, value = part.split("=", 1)
                    parts.append(key + "=" + (":".join(shorten(p) for p in value.split(":")) if key in linker_inputs else value))
                elif part.startswith("-L"):
                    parts.append("-L" + shorten(part[2:]))
                else:
                    parts.append(shorten(part))
            result.append("-Wl," + ",".join(parts))
        elif mode:
            result.append(shorten(argument) if mode == "input" else argument)
            mode = None
        elif argument in inputs or argument in runtime:
            result.append(argument)
            mode = "input" if argument in inputs else "runtime"
        elif argument.startswith(("--sysroot=", "-isystem", "-iquote", "-idirafter", "-include", "-imacros", "-I", "-L", "-B")):
            prefix = next(p for p in ("--sysroot=", "-isystem", "-iquote", "-idirafter", "-include", "-imacros", "-I", "-L", "-B") if argument.startswith(p))
            result.append(prefix + shorten(argument[len(prefix):]))
        else:
            result.append(shorten(argument))
    return result


def pkg_config_paths(arguments):
    """Extract header/search paths and absolute library operands from pkgconf argv."""
    options = ("-isystem", "-iquote", "-idirafter", "-I", "-L")
    paths = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        option = next((prefix for prefix in options if argument.startswith(prefix)), None)
        if option:
            value = argument[len(option):]
            if not value:
                index += 1
                if index == len(arguments):
                    raise ValueError("pkg-config path option has no argument: " + option)
                value = arguments[index]
            paths.append(value)
        elif argument.startswith("-Wl,"):
            paths.extend(value for value in argument.split(",")[1:] if Path(value).is_absolute())
        elif Path(argument).is_absolute():
            paths.append(argument)
        index += 1
    return paths


def validate_pkg_config_paths(arguments, root, inputs):
    # A directory's existence alone would accept host /usr/include. It must
    # contain declared files (or be within a declared tree artifact).
    root = Path(os.path.abspath(root))
    files = {Path(os.path.abspath(root / name)) for name in inputs}
    trees = {path for path in files if path.is_dir()}
    for value in pkg_config_paths(arguments):
        path = Path(os.path.abspath(root / value))
        accessible = path.is_relative_to(root) and path.exists() and (
            path in files
            or any(path.is_relative_to(tree) for tree in trees)
            or path.is_dir() and any(file.is_relative_to(path) for file in files)
        )
        if not accessible:
            raise ValueError("pkg-config path is not a declared accessible input: " + value)


def resolve_binutils(selected, compiler, root, inputs, environment):
    """Resolve legacy tool placeholders through the selected compiler only."""
    tools = {}
    queries = []
    declared = [Path(os.path.abspath(root / name)) for name in inputs]
    for key, value in selected.items():
        path = root / value
        if not value or not path.is_file():
            command = compiler + ["-print-prog-name=" + key.lower()]
            query = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True, check=True)
            value = query.stdout.strip()
            if not value or "\n" in value:
                raise ValueError("selected compiler returned no unique tool path for " + key)
            path = root / value
            queries.append({"tool": key, "command": command, "stdout": query.stdout, "stderr": query.stderr})
        if not path.is_file():
            raise ValueError("selected toolchain has no executable for " + key + ": " + str(path))
        # GCC can report the canonical repository path behind a sandbox input
        # symlink. Bind it back to an actual declared File, never to host PATH.
        normalized = Path(os.path.abspath(path))
        if normalized not in declared:
            physical = normalized.resolve()
            matches = [file for file in declared if file.is_file() and file.resolve() == physical]
            if not matches:
                raise ValueError("selected binutil is not a declared accessible input: " + str(path))
            normalized = matches[0]
        validate_pkg_config_paths([str(normalized)], root, inputs)
        tools[key] = [str(normalized)]
    return tools, queries


def compact_linker_arguments(arguments, response_directory):
    """Keep driver/source order; move only consecutive pure GNU ld words."""
    result = []
    pending = []
    directory = Path(response_directory)
    driver_values = {"-o", "-x", "-include", "-imacros", "-isystem", "-iquote", "-idirafter", "-I", "-B", "--sysroot", "-isysroot", "-D", "-U", "-MF", "-MT", "-MQ", "-dumpdir", "-dumpbase", "-dumpbase-ext"}

    def flush():
        if not pending:
            return
        content = "\n".join(shlex.quote(word) for word in pending) + "\n"
        name = "ld-" + hashlib.sha256(content.encode()).hexdigest() + ".rsp"
        path = directory / name
        if not path.exists():
            path.write_text(content)
        relative = os.path.relpath(path)
        if "," in relative:
            raise ValueError("GNU -Wl response path contains a comma: " + relative)
        result.append("-Wl,@" + relative)
        pending.clear()

    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument.startswith("-Wl,"):
            pending.extend(argument[4:].split(","))
        elif argument in ("-Xlinker", "-L", "-l", "-T", "-u", "-z"):
            index += 1
            if index == len(arguments):
                raise ValueError("link option has no argument: " + argument)
            if argument != "-Xlinker":
                pending.append(argument)
            pending.append(arguments[index])
        elif argument.startswith(("-L", "-l")) or re.search(r"\.(a|so(?:\.[A-Za-z0-9_.-]+)?)$", argument) and not argument.startswith("-"):
            pending.append(argument)
        else:
            flush()
            result.append(argument)
            if argument in driver_values:
                index += 1
                if index == len(arguments):
                    raise ValueError("compiler option has no argument: " + argument)
                result.append(arguments[index])
        index += 1
    flush()
    return result


def launcher(path, command, interpreter, link_flags=(), libraries=(), root=None):
    # Retain configure's cwd and forwarded source/output semantics. Relative
    # paths to declared inputs avoid GCC's single-environment-string limit
    # when COLLECT_GCC_OPTIONS would contain a long sandbox prefix per flag.
    text = "import hashlib,os,re,shlex,sys\nfrom pathlib import Path\na=sys.argv[1:]\n"
    if link_flags or libraries:
        text += inspect.getsource(compact_linker_arguments) + "\n"
    if root is not None:
        text += inspect.getsource(relative_arguments) + "\n"
        text += "root=" + repr(str(root)) + "\n"
        text += "cwd=os.getcwd()\n"
        text += "command=" + repr(command[:1]) + "+relative_arguments(" + repr(command[1:]) + ",root,cwd)\n"
    else:
        text += "command=" + repr(command) + "\n"
    if link_flags or libraries:
        text += "if not any(x in a for x in ('-c','-E','-S','--version','-v','-V','-qversion')):\n"
        if root is not None:
            text += "    command += relative_arguments(" + repr(list(link_flags)) + ",root,cwd)\n"
            text += "    a += relative_arguments(" + repr(list(libraries)) + ",root,cwd)\n"
        else:
            text += "    command += " + repr(list(link_flags)) + "\n    a += " + repr(list(libraries)) + "\n"
    if link_flags or libraries:
        text += "if not any(x in a for x in ('-c','-E','-S','--version','-v','-V','-qversion')):\n"
        text += "    command = compact_linker_arguments(command+a," + repr(str(path.parent)) + ")\n"
        text += "else:\n    command += a\n"
    else:
        text += "command += a\n"
    text += "os.execv(command[0],command)\n"

    program = path.with_suffix(".py")
    program.write_text(text)
    path.write_text("#!/bin/sh\nexec " + shlex.quote(str(interpreter)) + " " + shlex.quote(str(program)) + ' "$@"\n')
    path.chmod(0o755)


def main():
    root = Path.cwd()
    spec = json.loads(Path(sys.argv[1]).read_text())
    output = (root / sys.argv[2]).absolute()
    output.mkdir(exist_ok=True)
    work = output / "configuration"
    work.mkdir()
    launchers = output / "launchers"
    launchers.mkdir()
    runtime = root / spec["runtime"]
    file_command = root / spec["file_runtime"] / "bin/file"
    if not file_command.is_file():
        raise ValueError("declared file runtime provides no bin/file: " + str(file_command))
    tool_bin = runtime / "bin"
    compilers = {key: absolute_arguments(value, root) for key, value in spec["compilers"].items()}
    link_flags = absolute_arguments(spec["link_flags"], root)
    # APT GNU ld scripts contain declared execroot-relative filenames.
    # Configure runs out of tree; retain that search origin without changing
    # the source/output cwd observed by the compiler.
    link_flags.append("-L" + str(root))
    # rules_cc emits $ORIGIN-relative paths for Bazel's binary layout. Configure
    # executes conftest from its private output directory instead. Preserve those
    # toolchain flags and add the same declared libraries at their actual paths.
    for directory in spec["runtime_library_directories"]:
        link_flags.extend(["-Xlinker", "-rpath", "-Xlinker", str(root / directory)])
    libraries = absolute_arguments(spec["libraries"], root)

    for key, command in compilers.items():
        launcher(launchers / key, command, tool_bin / "python3", link_flags, libraries, root=root)
    launcher(launchers / "AR", [str(root / spec["ar"])], tool_bin / "python3", root=root)
    # The archiver implements ranlib's index operation; no ambient ranlib.
    launcher(launchers / "RANLIB", [str(root / spec["ar"]), "s"], tool_bin / "python3", root=root)
    binutils, tool_queries = resolve_binutils(
        spec["binutils"], [str(root / compilers["CC"][0]), *compilers["CC"][1:]], root, spec["toolchain_inputs"],
        {"LANG": "C", "LC_ALL": "C", "PATH": str(tool_bin)},
    )
    write_json(output / "binutils.json", {"tools": binutils, "compiler_queries": tool_queries})
    for key, command in binutils.items():
        launcher(launchers / key, command, tool_bin / "python3")
    runtime_manifest = json.loads((runtime / "toolchain.json").read_text())
    shell_command = [str(runtime / runtime_manifest["loader"]), "--library-path", os.pathsep.join(str(runtime / directory) for directory in runtime_manifest["library_directories"]), str(runtime / runtime_manifest["tools"]["bash"])]
    configure_shell = launchers / "SHELL"
    # The standard tool wrapper intentionally clears LD_LIBRARY_PATH. Configure
    # needs its declared native target closure for its child conftests. Keep the
    # shell itself on the execution runtime's fixed loader/library path instead.
    configure_shell.write_text("#!/bin/sh\nexec " + shlex.join(shell_command) + ' "$@"\n')
    configure_shell.chmod(0o755)
    directories = sorted({str((root / p).parent) for p in spec["pkg_config_files"]})
    environment = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC", "PATH": os.pathsep.join(dict.fromkeys([str((root / tool).parent) for tool in spec.get("extra_tools", [])] + [str(tool_bin)])),
                   "CONFIG_SHELL": str(configure_shell), "SHELL": str(configure_shell),
                   "CONFIG_SITE": "/dev/null", "PKG_CONFIG": str(tool_bin / "pkgconf"),
                   "FILECMD": str(file_command), "MAGIC_CMD": str(file_command),
                   "PKG_CONFIG_PATH": "", "PKG_CONFIG_LIBDIR": os.pathsep.join(directories),
                   "PKG_CONFIG_SYSROOT_DIR": str(root / spec["pkg_config_sysroot"]) if spec["pkg_config_sysroot"] else "",
                   "CPPFLAGS": "", "CFLAGS": "", "CXXFLAGS": "", "LDFLAGS": "", "LIBS": ""}
    if spec["configuration"]["build_triplet"] == spec["configuration"]["host_triplet"]:
        runtime_paths = list(dict.fromkeys(str(root / directory) for directory in spec["runtime_library_directories"]))
        if runtime_paths:
            environment["LD_LIBRARY_PATH"] = os.pathsep.join(runtime_paths)
        write_json(output / "native-runtime-paths.json", {"directories": runtime_paths, "bytes": len(environment.get("LD_LIBRARY_PATH", "").encode()), "source": "declared CcInfo runtime library directories; stable exact-directory deduplication"})
    if spec["pkg_config_fdo_sysroot_rules"]:
        environment["PKG_CONFIG_FDO_SYSROOT_RULES"] = "1"
    for key in (*compilers, "AR", "RANLIB", *binutils):
        environment[key] = shlex.quote(str(launchers / key))
    for key, value in spec["configuration"]["cache_answers"].items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or not ("_cv_" in key):
            raise ValueError("cache answer must name an Autoconf cache variable: " + key)
        environment[key] = value
    pkg_queries = []
    for path in spec["pkg_config_files"]:
        query = subprocess.run([environment["PKG_CONFIG"], "--cflags", "--libs", Path(path).stem],
                               env=environment, cwd=root, text=True, capture_output=True)
        if query.returncode:
            raise ValueError("declared pkg-config query failed: " + path + ": " + query.stderr)
        validate_pkg_config_paths(shlex.split(query.stdout), root, spec["inputs"])
        pkg_queries.append({"file": path, "stdout": query.stdout, "stderr": query.stderr})
    write_json(output / "pkg-config.json", pkg_queries)
    configuration = spec["configuration"]
    configure = root / spec["configure"]
    command = [environment["CONFIG_SHELL"], str(configure), "--srcdir=" + str(configure.parent),
               "--build=" + configuration["build_triplet"], "--host=" + configuration["host_triplet"],
               *configuration["configure_args"]]
    identities = []
    for name in sorted(set(spec["inputs"])):
        path = root / name
        members = sorted(path.rglob("*")) if path.is_dir() else [path]
        for member in members:
            if member.is_file():
                with member.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                identities.append({"path": str(member.relative_to(root)), "sha256": digest})
    identity = {"schema": 1, "inputs": identities, "configuration": configuration, "target": spec["target"],
                "toolchain": {key: spec[key] for key in ("compilers", "link_flags", "libraries", "ar", "binutils")},
                "pkg_config": {"files": spec["pkg_config_files"], "sysroot": spec["pkg_config_sysroot"],
                               "fdo_sysroot_rules": spec["pkg_config_fdo_sysroot_rules"]},
                "expected_outputs": spec["expected_outputs"], "extra_tools": spec.get("extra_tools", [])}
    identity["probe_runner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity["fingerprint"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    write_json(output / "identity.json", identity)
    write_json(output / "commands.json", {"schema": 1, "configure": command, "configure_shell": shell_command, "file_command": str(file_command), "compilers": compilers, "binutils": binutils, "binutils_queries": tool_queries,
                                          "linker": [compilers["CC"][0], *link_flags, *libraries]})
    write_json(output / "environment.json", {"schema": 1, "environment": environment})
    with (work / "configure.stdout").open("w") as stdout, (work / "configure.stderr").open("w") as stderr:
        result = subprocess.run(command, cwd=work, env=environment, stdout=stdout, stderr=stderr)
    if result.returncode:
        sys.stderr.write((work / "configure.stderr").read_text())
        log = work / "config.log"
        if log.exists():
            sys.stderr.write(log.read_text())
        raise ValueError("configure failed with exit " + str(result.returncode))
    for name in spec["expected_outputs"]:
        path = work / name
        if path.is_absolute() and not path.is_relative_to(work) or ".." in Path(name).parts:
            raise ValueError("expected output escapes configuration directory: " + name)
        if not path.is_file():
            raise ValueError("configure did not produce expected output: " + name)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("autoconf-probe: " + str(error))
