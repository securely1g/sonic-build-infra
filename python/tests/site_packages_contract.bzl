"""Source distribution declaration shared by wheel and direct installation."""

load("@rules_python//python:packaging.bzl", "py_wheel")
load("//python:site_packages.bzl", "site_packages")

DEMO_METADATA = {
    "distribution": "site-demo",
    "version": "2.4.1",
    "python_requires": ">=3.11",
    "requires": ["leaf-package>=1.0; python_version >= '3.11'"],
    "extra_requires": {"metrics": ["monitor>=2"]},
    "entry_points": {"console_scripts": ["site-demo = site_demo.cli:main"]},
    "summary": "Direct source installation fixture",
    "classifiers": ["Programming Language :: Python :: 3", "Topic :: Software Development"],
    "project_urls": {"Documentation": "https://example.org/docs", "Source": "https://example.org/source"},
    "description_file": "site_demo/README.md",
    "description_content_type": "text/markdown",
    "extra_distinfo_files": {"site_demo/LICENSE": "licenses/LICENSE"},
    "license_files": ["LICENSE"],
    "maintainer": "Fixture Maintainer",
    "maintainer_email": "maintainer@example.org",
    "keywords": "network,metadata",
    "regular_file_mode": "0644",
    "python_tag": "py3",
}

def demo_targets(name):
    """Declare a wheel and source installation from the same fixture metadata.

    Args:
        name: Source installation target name.
    """
    py_wheel(
        name = name + "_wheel",
        deps = [":demo_library"],
        strip_path_prefixes = ["python/tests"],
        **DEMO_METADATA
    )

    site_packages(
        name = name,
        files = {
            "site_demo/__init__.py": "site_demo/__init__.py",
            "site_demo/cli.py": "site_demo/cli.py",
            "site_demo/default.json": "site_demo/default.json",
            ":generated_data": "site_demo/generated.txt",
        },
        site_packages = "/opt/runtime/site",
        scripts = "/opt/runtime/bin",
        headers = "/opt/runtime/include",
        data_prefix = "/opt/runtime",
        interpreter = "/usr/bin/python3",
        runtime_deps = [":leaf"],
        **DEMO_METADATA
    )
