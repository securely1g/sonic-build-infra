"""Generate and check image Python locks from declared source distributions.

PEP 508 requirements, versions and marker evaluation use the pinned packaging
library. Only the explicit update command invokes uv or writes workspace locks.
"""

import argparse
import hashlib
import json
import os
from email.parser import BytesParser
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version


class RuntimeLockError(ValueError):
    """The declared environment or its reviewed lock is inconsistent."""


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def target_environment(python_version, python_platform, overrides=None):
    version = Version(python_version)
    if len(version.release) < 2:
        raise RuntimeLockError("python_version must identify major and minor versions")
    full = ".".join(str(n) for n in (version.release + (0, 0, 0))[:3])
    if python_platform in ("linux", "x86_64-unknown-linux-gnu"):
        machine = "x86_64"
    elif python_platform.startswith(("x86_64-", "aarch64-", "armv7-", "i686-")) and "linux" in python_platform:
        machine = python_platform.split("-", 1)[0]
    else:
        raise RuntimeLockError("provide a supported Linux target triple: " + python_platform)
    environment = {
        "implementation_name": "cpython",
        "implementation_version": full,
        "os_name": "posix",
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": "Linux",
        "platform_version": "",
        "python_full_version": full,
        "python_version": ".".join(str(n) for n in version.release[:2]),
        "sys_platform": "linux",
    }
    environment.update(overrides or {})
    return environment


def applies(requirement, environment, extras):
    if requirement.marker is None:
        return True
    return any(requirement.marker.evaluate(dict(environment, extra=extra)) for extra in (set(extras) | {""}))


def external_requests(plan):
    """Select the source closure and return concrete-target external requests."""
    sources = {}
    for source in plan["sources"]:
        name = canonicalize_name(source["distribution"])
        if name in sources and sources[name] != source:
            raise RuntimeLockError("conflicting source producer for " + name)
        Version(source["version"])
        sources[name] = source
    declared_sources = {canonicalize_name(name) for name in plan.get("source_distributions", [])} | set(sources)
    environment = target_environment(plan["python_version"], plan["python_platform"], plan.get("marker_environment"))
    selected_extras = {}
    queue = [(Requirement(root), set()) for root in plan["roots"]]
    external = set()
    selected = set()
    while queue:
        requirement, context_extras = queue.pop(0)
        if not applies(requirement, environment, context_extras):
            continue
        name = canonicalize_name(requirement.name)
        if name not in sources:
            if name in declared_sources:
                raise RuntimeLockError("missing source producer for " + name)
            # This plan fixes the full marker environment. Raw marker spelling
            # remains in sources/roots for freshness; uv receives active requests.
            requirement.marker = None
            external.add(str(requirement))
            continue
        source = sources[name]
        if requirement.url:
            raise RuntimeLockError("source distribution cannot be replaced by URL: " + name)
        if not requirement.specifier.contains(source["version"], prereleases=True):
            raise RuntimeLockError(f"source version {name}=={source['version']} does not satisfy {requirement}")
        if not SpecifierSet(source.get("requires_python", "")).contains(environment["python_full_version"], prereleases=True):
            raise RuntimeLockError(f"source {name} requires Python {source['requires_python']}")
        requested = {canonicalize_name(extra) for extra in requirement.extras}
        available = {canonicalize_name(extra) for extra in source.get("extras", [])}
        unknown = requested - available
        if unknown:
            raise RuntimeLockError(f"source {name} does not declare extras: {', '.join(sorted(unknown))}")
        previous = selected_extras.get(name)
        combined = requested | (previous or set())
        if previous is not None and previous == combined:
            continue
        selected_extras[name] = combined
        selected.add(name)
        queue.extend((Requirement(raw), combined) for raw in source.get("requires_dist", []))
    return sorted(external), sorted(selected)


def make_plan(manifest):
    """Read declared metadata/constraints into a location-independent plan."""
    plan = {key: manifest[key] for key in [
        "roots", "python_version", "python_platform", "uv_version", "index_urls",
        "find_links", "no_index", "marker_environment", "source_distributions",
    ]}
    if not plan["uv_version"]:
        raise RuntimeLockError("uv_version must be fixed by the lock owner")
    for url in plan["index_urls"]:
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            raise RuntimeLockError("index credentials must be supplied outside lock inputs")
    plan["schema_version"] = 1
    sources = {}
    for item in manifest["metadata"]:
        source = read_json(item["path"])
        source["producer"] = item["producer"]
        name = canonicalize_name(source["distribution"])
        if name in sources and sources[name] != source:
            raise RuntimeLockError("conflicting source producer for " + name)
        sources[name] = source
    plan["sources"] = [sources[name] for name in sorted(sources)]
    plan["constraints"] = [{"label": item["label"], "content": Path(item["path"]).read_text()}
                           for item in manifest["constraints"]]
    requests, selected = external_requests(plan)
    plan["external_requirements"] = requests
    plan["selected_sources"] = selected
    return plan


def requirements_text(plan):
    requests, _ = external_requests(plan)
    return "# Generated from source Requires-Dist for this target environment.\n" + "".join(value + "\n" for value in requests)


def locked_packages(content):
    """Parse uv's hash-locked requirements output with standard requirements."""
    logical = content.replace("\\\n", " ")
    packages = {}
    for line in logical.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            raise RuntimeLockError("unexpected resolver directive in reviewed lock: " + line)
        hashes = re.findall(r"--hash=sha256:([a-f0-9]{64})(?=\s|$)", line)
        requirement_text = re.sub(r"\s+--hash=\S+", "", line).strip()
        requirement = Requirement(requirement_text)
        if not hashes:
            raise RuntimeLockError("locked distribution has no SHA256 hashes: " + str(requirement))
        pins = list(requirement.specifier)
        if not requirement.url and (len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version):
            raise RuntimeLockError("locked distribution needs an exact version: " + str(requirement))
        name = canonicalize_name(requirement.name)
        record = {"requirement": str(requirement), "version": pins[0].version if pins else None,
                  "url": requirement.url, "hashes": sorted(set(hashes))}
        if name in packages and packages[name] != record:
            raise RuntimeLockError("duplicate locked distribution: " + name)
        packages[name] = record
    return packages


def validate_lock(plan, content):
    packages = locked_packages(content)
    requests, _ = external_requests(plan)
    for raw in requests:
        requirement = Requirement(raw)
        name = canonicalize_name(requirement.name)
        if name not in packages:
            raise RuntimeLockError("runtime lock lacks required distribution: " + name)
        package = packages[name]
        if requirement.url:
            if package["url"] != requirement.url:
                raise RuntimeLockError("runtime lock has a different source URL: " + name)
        elif package["version"] is None or not requirement.specifier.contains(package["version"], prereleases=True):
            raise RuntimeLockError("runtime lock does not satisfy " + str(requirement))
    return packages


def check(plan, requirements, lock, inputs):
    """Check read-only inputs; never invoke a resolver or modify the lock."""
    for path in (requirements, lock, inputs):
        if not Path(path).is_file():
            raise RuntimeLockError("missing runtime lock input: " + str(path))
    record = read_json(inputs)
    if record.get("schema_version") != 1 or record.get("plan_sha256") != digest(canonical_json(plan)):
        raise RuntimeLockError("runtime lock is stale: source metadata or target environment changed")
    wanted = requirements_text(plan).encode()
    actual = Path(requirements).read_bytes()
    if actual != wanted or record.get("requirements_sha256") != digest(actual):
        raise RuntimeLockError("runtime requests are stale or modified")
    lock_bytes = Path(lock).read_bytes()
    if record.get("lock_sha256") != digest(lock_bytes):
        raise RuntimeLockError("reviewed runtime lock content changed")
    validate_lock(plan, lock_bytes.decode())
    return record



def installed_distributions(archives):
    result = {}
    for path in archives:
        with tarfile.open(path) as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith(".dist-info/METADATA"):
                    continue
                metadata = BytesParser().parsebytes(archive.extractfile(member).read())
                name = canonicalize_name(metadata["Name"] or "")
                version = metadata["Version"]
                if not name or not version:
                    raise RuntimeLockError("installed distribution lacks Name/Version: " + str(path))
                record = {"version": str(Version(version)),
                          "requires_dist": sorted(str(Requirement(value)) for value in metadata.get_all("Requires-Dist", [])),
                          "requires_python": str(SpecifierSet(metadata["Requires-Python"] or "")),
                          "extras": sorted(canonicalize_name(value) for value in metadata.get_all("Provides-Extra", []))}
                if name in result:
                    raise RuntimeLockError("duplicate installed distribution: " + name)
                result[name] = record
    return result


def verify_installation(plan, lock_content, source_tars, third_party_tars):
    expected_external = validate_lock(plan, lock_content)
    expected_sources = {canonicalize_name(source["distribution"]): source for source in plan["sources"]
                        if canonicalize_name(source["distribution"]) in plan["selected_sources"]}
    sources = installed_distributions(source_tars)
    external = installed_distributions(third_party_tars)
    if set(sources) != set(expected_sources):
        raise RuntimeLockError("source installation set differs from plan: expected " +
                               repr(sorted(expected_sources)) + "; got " + repr(sorted(sources)))
    if set(external) != set(expected_external):
        raise RuntimeLockError("third-party installation set differs from lock: expected " +
                               repr(sorted(expected_external)) + "; got " + repr(sorted(external)))
    if set(sources) & set(external):
        raise RuntimeLockError("a source distribution was also installed from an external wheel")
    for name, source in expected_sources.items():
        expected = {"version": str(Version(source["version"])),
                    "requires_dist": sorted(str(Requirement(value)) for value in source.get("requires_dist", [])),
                    "requires_python": str(SpecifierSet(source.get("requires_python", ""))),
                    "extras": sorted(canonicalize_name(value) for value in source.get("extras", []))}
        if sources[name] != expected:
            raise RuntimeLockError("source installation metadata differs from producer: " + name)
    for name, package in expected_external.items():
        if package["version"] is not None and external[name]["version"] != str(Version(package["version"])):
            raise RuntimeLockError("installed third-party version differs from lock: " + name)
    installed = dict(sources, **external)
    installed_plan = dict(plan, sources=[dict(record, distribution=name) for name, record in installed.items()],
                          source_distributions=list(installed))
    missing, _ = external_requests(installed_plan)
    if missing:
        raise RuntimeLockError("installed runtime lacks transitive requirements: " + ", ".join(missing))

def _stage_output(path, contents, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            os.chmod(temporary, mode)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _publish(files):
    """Stage the complete result, restoring prior outputs on publication failure."""
    originals = {Path(path): Path(path).read_bytes() if Path(path).exists() else None for path in files}
    modes = {path: path.stat().st_mode & 0o777 if path.exists() else 0o644 for path in originals}
    staged = {}
    try:
        for path, contents in files.items():
            path = Path(path)
            staged[path] = _stage_output(path, contents, modes[path])
        for path, temporary in staged.items():
            os.replace(temporary, path)
    except BaseException:
        for path, contents in originals.items():
            if contents is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != contents or path.stat().st_mode & 0o777 != modes[path]:
                # Replacing the file also restores read-only inputs. Opening
                # the already-published destination for writing cannot do so.
                backup = _stage_output(path, contents, modes[path])
                try:
                    os.replace(backup, path)
                finally:
                    backup.unlink(missing_ok=True)
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def update(plan, requirements, lock, inputs, uv, cache_dir=None):
    """Resolve with the configured uv, preserving the old lock as the baseline."""
    outputs = [Path(path).resolve() for path in (requirements, lock, inputs)]
    if len(set(outputs)) != len(outputs):
        raise RuntimeLockError("requirements, lock and inputs must be different files")
    version = subprocess.run([str(uv), "--version"], check=True, text=True, capture_output=True).stdout.split()
    if len(version) < 2 or version[:2] != ["uv", plan["uv_version"]]:
        raise RuntimeLockError(f"expected uv {plan['uv_version']}; got {' '.join(version)}")
    requests = requirements_text(plan).encode()
    old_lock = Path(lock).read_bytes() if Path(lock).is_file() else b""
    with tempfile.TemporaryDirectory(prefix="python-runtime-lock-") as directory:
        stage = Path(directory)
        request_file, lock_file = stage / "requirements.in", stage / "requirements_lock.txt"
        request_file.write_bytes(requests)
        if old_lock:
            lock_file.write_bytes(old_lock)
        command = [str(uv)]
        if cache_dir:
            command += ["--cache-dir", str(cache_dir)]
        command += ["pip", "compile", str(request_file), "--python-version", plan["python_version"],
                    "--python-platform", plan["python_platform"], "--generate-hashes", "--no-header",
                    "--no-annotate", "--no-emit-index-url", "--output-file", str(lock_file)]
        if plan["no_index"]:
            command.append("--no-index")
        for index, url in enumerate(plan["index_urls"]):
            command += ["--index-url" if index == 0 else "--extra-index-url", url]
        for link in plan["find_links"]:
            command += ["--find-links", link]
        for index, constraint in enumerate(plan["constraints"]):
            path = stage / f"constraints-{index}.txt"
            path.write_text(constraint["content"])
            command += ["--constraint", str(path)]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeLockError("uv dependency resolution failed:\n" + result.stderr)
        locked = lock_file.read_bytes()
        packages = validate_lock(plan, locked.decode())
        record = {"schema_version": 1, "plan_sha256": digest(canonical_json(plan)),
                  "requirements_sha256": digest(requests), "lock_sha256": digest(locked),
                  "uv_version": plan["uv_version"], "packages": packages}
        _publish({Path(requirements): requests, Path(lock): locked,
                  Path(inputs): json.dumps(record, sort_keys=True, indent=2).encode() + b"\n"})
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("plan")
    export.add_argument("--manifest", required=True)
    export.add_argument("--output", required=True)
    for name in ("check", "update"):
        command = commands.add_parser(name)
        for field in ("plan", "requirements", "lock", "inputs"):
            command.add_argument("--" + field, required=True)
        if name == "update":
            command.add_argument("--uv", required=True)
            command.add_argument("--cache-dir")
        else:
            command.add_argument("--validation-tar")
            command.add_argument("--source-tar", action="append", default=[])
            command.add_argument("--third-party-tar", action="append", default=[])
            command.add_argument("--verify-installation", action="store_true")
            command.add_argument("--update-command", default="the owner runtime-lock update entry")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan = make_plan(read_json(args.manifest))
            Path(args.output).write_bytes(json.dumps(plan, sort_keys=True, indent=2).encode() + b"\n")
        elif args.command == "update":
            update(read_json(args.plan), args.requirements, args.lock, args.inputs, args.uv, args.cache_dir)
        else:
            plan = read_json(args.plan)
            check(plan, args.requirements, args.lock, args.inputs)
            if args.verify_installation:
                verify_installation(plan, Path(args.lock).read_text(), args.source_tar, args.third_party_tar)
            if args.validation_tar:
                with tarfile.open(args.validation_tar, "w", format=tarfile.GNU_FORMAT):
                    pass
    except (RuntimeLockError, ValueError, OSError, subprocess.CalledProcessError) as error:
        message = str(error)
        if args.command == "check":
            message += "\nUpdate with: " + args.update_command
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
