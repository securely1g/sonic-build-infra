"""Combine pinned package payloads only when their overlapping files agree."""

def _inventory(ctx, tar, archive):
    # libarchive owns archive decoding. Request flat mtree metadata, without
    # timestamps: Debian multiarch packages may have different build times.
    result = ctx.execute([
        str(tar),
        "--create",
        "--format=mtree",
        "--options=mtree:!all,mtree:type,mtree:mode,mtree:uid,mtree:gid,mtree:link",
        "--file",
        "-",
        "@" + str(archive),
    ])
    if result.return_code:
        fail("Cannot inspect sysroot archive {}: {}".format(archive, result.stderr))
    entries = {}
    for line in result.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split(" ")
        path = fields[0]
        if path in ["/.", ".", "./"]:
            continue

        # These pinned Debian payloads use plain relative paths. Fail rather
        # than silently misinterpret a future escaped name or mtree directive.
        if not path.startswith("./") or "\\" in line or "" in fields:
            fail("Unsupported sysroot inventory record: " + line)
        path = path[2:]
        if any([part in ["", ".", ".."] for part in path.split("/")]):
            fail("Unsafe sysroot path: " + path)
        metadata = {}
        for field in fields[1:]:
            key, separator, value = field.partition("=")
            if not separator or key in metadata:
                fail("Invalid sysroot inventory record: " + line)
            metadata[key] = value
        if any([key not in metadata for key in ["type", "mode", "uid", "gid"]]):
            fail("Incomplete sysroot inventory record: " + line)
        if metadata["type"] not in ["file", "dir", "link"]:
            fail("Unsupported sysroot file type: " + line)
        if metadata["type"] == "link":
            target = metadata.get("link", "")
            depth = len(path.split("/")) - 1
            if not target or target.startswith("/"):
                fail("Non-relative sysroot symlink: " + line)
            for part in target.split("/"):
                depth += -1 if part == ".." else (0 if part in ["", "."] else 1)
                if depth < 0:
                    fail("Sysroot symlink escapes the root: " + line)
        if path in entries:
            fail("Repeated path within sysroot archive {}: {}".format(archive, path))
        entries[path] = metadata
    return entries

def extract_archives(ctx, tar, archives, output):
    """Check shared metadata and exact file bytes before overlaying archives.

    Args:
        ctx: Repository context that owns the output and staging directory.
        tar: Existing pinned BSD tar executable, used only for metadata.
        archives: Labels of existing Distroless data.tar.xz source files.
        output: Relative directory for the combined payload.

    Returns:
        Counts of the paths and identical overlaps verified during assembly.
    """
    inventories = []
    owners = {}
    shared = {}
    for label in archives:
        archive = ctx.path(label)
        if not archive.exists or archive.is_dir or archive.basename != "data.tar.xz":
            fail("Expected pinned Distroless repository source archive: {}".format(label))
        entries = _inventory(ctx, tar, archive)
        for path, metadata in entries.items():
            if path in owners:
                previous, previous_metadata = owners[path]
                if metadata != previous_metadata:
                    fail("Sysroot metadata conflict at {} between {} and {}: {} != {}".format(
                        path,
                        previous,
                        label,
                        previous_metadata,
                        metadata,
                    ))
                shared[path] = metadata["type"]
            else:
                owners[path] = (str(label), metadata)
        inventories.append((archive, entries))

    # A symlink or regular file must never become a parent directory during
    # extraction, even when its children come from a different package.
    for path in owners:
        parts = path.split("/")
        for end in range(1, len(parts)):
            parent = "/".join(parts[:end])
            if parent in owners and owners[parent][1]["type"] != "dir":
                fail("Sysroot path {} has non-directory parent {}".format(path, parent))

    extracted_files = {}
    comparisons = 0
    for archive, entries in inventories:
        duplicates = [path for path, metadata in entries.items() if metadata["type"] == "file" and path in extracted_files]
        if duplicates:
            ctx.extract(archive, output = "_sysroot_staging")
            for path in duplicates:
                # Bazel 8.5.1 read() preserves bytes using ISO-8859-1, including
                # binary and compressed files. Do not substitute mtree checksums:
                # the pinned BSD tar build does not emit cryptographic hashes.
                if ctx.read("_sysroot_staging/" + path) != ctx.read(output + "/" + path):
                    fail("Sysroot content conflict at {} between {} and {}".format(
                        path,
                        extracted_files[path],
                        archive,
                    ))
                comparisons += 1
            ctx.delete("_sysroot_staging")
        ctx.extract(archive, output = output)
        for path, metadata in entries.items():
            if metadata["type"] == "file":
                extracted_files[path] = str(archive)

    return {
        "paths": len(owners),
        "shared_paths": len(shared),
        "shared_files": len([kind for kind in shared.values() if kind == "file"]),
        "shared_symlinks": len([kind for kind in shared.values() if kind == "link"]),
        "shared_directories": len([kind for kind in shared.values() if kind == "dir"]),
        "file_comparisons": comparisons,
        "comparison": "exact bytes",
    }
