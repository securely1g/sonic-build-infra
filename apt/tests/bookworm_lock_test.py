"""Check the reviewed Bookworm graph used by native library/tool consumers."""

import argparse
import json
from pathlib import Path

import lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("sources", type=Path)
    parser.add_argument("requests", type=Path)
    args = parser.parse_args()
    document, sources, requests = [json.loads(path.read_text()) for path in (args.lock, args.sources, args.requests)]
    lock.check_lock(document, sources, requests)
    collections = {}
    for name in ["bookworm_toolchains", "bookworm_build_tools", "bookworm_target_debug"]:
        definition = document["dependency_sets"][name]
        assert set(definition["sets"]) == {"amd64", "arm64"}, name
        closure = lock.reachable(dict(document, dependency_sets={name: definition}))
        assert all(document["packages"][key]["suite"] in {"bookworm", "bookworm-updates", "bookworm-security"} for key in closure), name
        for key in closure:
            assert isinstance(document["packages"][key].get("files"), list), "Missing native file map: " + key
        collections[name] = definition

    def selected(group, architecture, package):
        roots = collections[group]["sets"][architecture]
        matches = [document["packages"][key + "=" + version] for key, version in roots.items()
                   if document["packages"][key + "=" + version]["name"] == package]
        assert len(matches) == 1, (group, architecture, package)
        return matches[0]

    for architecture in ["amd64", "arm64"]:
        development = selected("bookworm_build_tools", architecture, "libperl-dev")
        # Debian ships the embedding headers in libperl5.36, reached through
        # libperl-dev's native dependency exports.
        dependencies = [document["packages"][key] for key in development["depends_on"]]
        assert any(path.endswith("/CORE/perl.h") for package in dependencies for path in package["files"]), architecture
        assert any(path.endswith("/libperl.so") for path in development["files"]), architecture
        libc = selected("bookworm_toolchains", architecture, "libc6")
        libc_development = selected("bookworm_toolchains", architecture, "libc6-dev")
        libc_dependencies = [document["packages"][key] for key in libc_development["depends_on"]]
        assert libc in libc_dependencies, "libc6-dev must export the matching libc6 linker inputs: " + architecture
        assert libc_development not in libc_dependencies, "libc6-dev must not depend on itself: " + architecture
        debug = selected("bookworm_target_debug", architecture, "libc6-dbg")
        assert libc["version"] == debug["version"], (architecture, libc["version"], debug["version"])
        assert libc["architecture"] == debug["architecture"] == architecture
    print("Bookworm AMD64/ARM64 package hashes, closures, native file maps and matching libc debug versions verified")


if __name__ == "__main__":
    main()
