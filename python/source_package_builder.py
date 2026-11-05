"""Install declared package files directly using PyPA installer's source API."""

from email.message import EmailMessage
from email.policy import default
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

from installer import install
from installer.sources import WheelSource

from wheel_layer_builder import LayerDestination, absolute_directory


def relative_path(value):
    path = PurePosixPath(value)
    if not value or value in (".", "..") or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("Expected a package-relative file path: " + value)
    return str(path)


def header(message, name, value):
    if "\n" in value or "\r" in value:
        raise ValueError("Newline in metadata field " + name)
    message[name] = value


class PackageSource(WheelSource):
    """Expose declared files and generated metadata without a wheel ZIP step."""

    def __init__(self, spec):
        distribution = spec["distribution"]
        version = spec["version"]
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", distribution):
            raise ValueError("Invalid distribution: " + distribution)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_]*", version):
            raise ValueError("Expected a normalized package version: " + version)
        super().__init__(re.sub(r"[-_.]+", "_", distribution).lower(), version)
        self.regular_file_mode = spec.get("regular_file_mode", "")
        if self.regular_file_mode not in ("", "0644", "0755"):
            raise ValueError("regular_file_mode must be empty, 0644 or 0755")
        self.files = {}
        for item in spec["files"]:
            path = relative_path(item["path"])
            if path in self.files or any(part.endswith(".dist-info") for part in PurePosixPath(path).parts):
                raise ValueError("Duplicate or reserved package path: " + path)
            self.files[path] = Path(item["source"])
        metadata = EmailMessage(policy=default.clone(max_line_length=0, utf8=True))
        license_files = [relative_path(value) for value in spec.get("license_files", [])]
        for name, value in [("Metadata-Version", "2.4" if license_files else "2.1"), ("Name", distribution), ("Version", version)]:
            header(metadata, name, value)
        for key, name in [("python_requires", "Requires-Python"), ("summary", "Summary"),
                          ("license", "License"), ("author", "Author"),
                          ("author_email", "Author-email"), ("homepage", "Home-page"),
                          ("maintainer", "Maintainer"), ("maintainer_email", "Maintainer-email"),
                          ("keywords", "Keywords"),
                          ("description_content_type", "Description-Content-Type")]:
            if spec.get(key):
                header(metadata, name, spec[key])
        for classifier in spec.get("classifiers", []):
            header(metadata, "Classifier", classifier)
        for label, url in spec.get("project_urls", {}).items():
            header(metadata, "Project-URL", label + ", " + url)
        for license_file in license_files:
            header(metadata, "License-File", license_file)
        requires = list(spec.get("requires", []))
        for extra, requirements in spec.get("extra_requires", {}).items():
            header(metadata, "Provides-Extra", extra)
            for requirement in requirements:
                base, separator, marker = requirement.partition(";")
                condition = f"({marker.strip()}) and extra == '{extra}'" if separator else f"extra == '{extra}'"
                requires.append(f"{base.strip()}; {condition}")
        for requirement in requires:
            header(metadata, "Requires-Dist", requirement)
        pure = spec.get("abi", "none") == "none" and spec.get("platform", "any") == "any"
        description = Path(spec["description_file"]).read_text(encoding="utf-8") if spec.get("description_file") else ""
        self.generated = {
            "METADATA": metadata.as_bytes() + description.encode("utf-8"),
            "WHEEL": ("Wheel-Version: 1.0\nGenerator: sonic-build-infra\n"
                      f"Root-Is-Purelib: {str(pure).lower()}\n"
                      f"Tag: {spec.get('python_tag', 'py3')}-{spec.get('abi', 'none')}-{spec.get('platform', 'any')}\n").encode(),
        }
        for item in spec.get("extra_distinfo_files", []):
            path = relative_path(item["path"])
            if path in self.generated or path in ("RECORD", "entry_points.txt", "INSTALLER"):
                raise ValueError("Reserved or duplicate dist-info path: " + path)
            self.generated[path] = Path(item["source"]).read_bytes()
        for license_file in license_files:
            if "licenses/" + license_file not in self.generated:
                raise ValueError("License-File requires an extra_distinfo_files entry under licenses/: " + license_file)
        if spec.get("entry_points"):
            lines = []
            for group, entries in sorted(spec["entry_points"].items()):
                if any(char in group for char in "\r\n[]"):
                    raise ValueError("Invalid entry point group")
                lines += [f"[{group}]"]
                for entry in sorted(entries):
                    if "\n" in entry or "\r" in entry:
                        raise ValueError("Invalid entry point")
                    lines.append(entry)
                lines.append("")
            self.generated["entry_points.txt"] = "\n".join(lines).encode()
        self.runtime = {
            "distribution": distribution,
            "version": version,
            "requires_python": spec.get("python_requires", ""),
            "requires_dist": requires,
            "extras": list(spec.get("extra_requires", {})),
            "entry_points": spec.get("entry_points", {}),
            "python_tag": spec.get("python_tag", "py3"),
            "abi": spec.get("abi", "none"),
            "platform": spec.get("platform", "any"),
        }

    @property
    def dist_info_filenames(self):
        return list(self.generated)

    def read_dist_info(self, filename):
        return self.generated[filename].decode()

    def get_contents(self):
        for name, path in sorted(self.files.items()):
            with path.open("rb") as stream:
                executable = self.regular_file_mode == "0755" if self.regular_file_mode else bool(path.stat().st_mode & 0o111)
                yield (name, "", ""), stream, executable
        for name, contents in sorted(self.generated.items()):
            with BytesIO(contents) as stream:
                yield (f"{self.dist_info_dir}/{name}", "", ""), stream, False


def mtree_escape(value):
    # BSD mtree uses octal escapes for whitespace, backslash and comment bytes.
    return "".join(f"\\{ord(c):03o}" if c.isspace() or c in "\\#=" else c for c in value)


def build_installation(spec, tree, mtree, metadata):
    source = PackageSource(spec)
    scheme = {key: absolute_directory(spec[attr]) for key, attr in [
        ("purelib", "site_packages"), ("platlib", "site_packages"), ("scripts", "scripts"),
        ("headers", "headers"), ("data", "data_prefix"),
    ]}
    interpreter = absolute_directory(spec["interpreter"])
    tree = Path(tree)
    tree.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o022)
    try:
        destination = LayerDestination(scheme_dict=scheme, interpreter=interpreter,
                                       script_kind="posix", bytecode_optimization_levels=(),
                                       destdir=str(tree))
        install(source, destination, additional_metadata={"INSTALLER": b"sonic-build-infra\n"})
    finally:
        os.umask(previous_umask)
    lines = ["#mtree"]
    for path in sorted(tree.rglob("*")):
        name = mtree_escape("./" + path.relative_to(tree).as_posix())
        if path.is_dir():
            lines.append(f"{name} type=dir uid=0 gid=0 mode=0755 time=0")
        else:
            mode = "0755" if path.stat().st_mode & 0o111 else "0644"
            lines.append(f"{name} type=file uid=0 gid=0 mode={mode} time=0 content={mtree_escape(path.as_posix())}")
    Path(mtree).write_text("\n".join(lines) + "\n")
    Path(metadata).write_text(json.dumps(source.runtime, sort_keys=True) + "\n")


if __name__ == "__main__":
    manifest, tree, mtree, metadata = sys.argv[1:]
    build_installation(json.loads(Path(manifest).read_text()), tree, mtree, metadata)
