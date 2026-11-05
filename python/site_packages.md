# Python source installation TARs

`site_packages` builds one distribution's installation TAR directly from declared
files. It does not build or unpack a wheel, resolve pip dependencies, or import
package code. Installation uses PyPA installer to generate scripts and installed
`RECORD`; `tar.bzl` archives the resulting layout with uid/gid and timestamps zero.
The build tool runs in the execution configuration. Target paths and shebangs are
explicit inputs; the rule does not change the selected platform or C/C++ toolchain.

## Declare package contents and metadata

Load `site_packages` from `//python:site_packages.bzl`. In a component `.bzl`
macro, share the supported metadata attributes with the wheel declaration:

```starlark
load("@rules_python//python:packaging.bzl", "py_wheel")
load("@sonic_build_infra//python:site_packages.bzl", "site_packages")

_METADATA = {
    "distribution": "example-service",
    "version": "1.2.3",
    "requires": ["example-common>=1.0; python_version >= '3.11'"],
    "python_requires": ">=3.11",
    "entry_points": {"console_scripts": ["example = example.cli:main"]},
}

def example_distribution():
    py_wheel(name = "wheel", deps = [":package_files"], **_METADATA)
    site_packages(
        name = "runtime",
        files = {
            "example/__init__.py": "example/__init__.py",
            "example/cli.py": "example/cli.py",
            "example/default.json": "example/default.json",
        },
        site_packages = "/usr/lib/python3/dist-packages",
        scripts = "/usr/local/bin",
        headers = "/usr/local/include",
        data_prefix = "/usr/local",
        interpreter = "/usr/bin/python3",
        runtime_deps = ["@example_common//:runtime"],
        **_METADATA
    )
```

`files` maps single-file labels (including generated files and extensions) to
package-relative paths. It does not walk `PyInfo` dependencies. Resources are
explicit files. To install outside site-packages, use standard wheel content
paths `<normalized_distribution>-<version>.data/{scripts,headers,data}/...`.
The shared metadata attributes are `distribution`, `version`, `requires`,
`python_requires`, `extra_requires`, `entry_points`, `python_tag`, `abi`,
`platform`, `summary`, `license`, `author`, `author_email`, and `homepage`.
Use a normalized version and target-appropriate tags. A component's wheel rule
must also support its extension/tag requirements; this interface does not add
extension support to upstream `py_wheel`.

Without `distribution`, existing `site_packages(srcs=[...])` calls retain the
legacy source-aggregation behavior. New distribution installs use explicit
`files` and metadata. The legacy API does not provide distribution metadata.

## Runtime handoff to image assembly

The target's default output is **only this distribution's `.tar`**. Runtime
requirements are preserved, not resolved. `runtime_deps` links source package
installation targets; it never merges their files into this distribution.
`PythonRuntimeInfo` from `//python:source_package.bzl` exposes:

- `packages`: transitive structs with `distribution`, `version`, `producer`, `tar`, `metadata`.
- `tars`: transitive installation TARs.
- `metadata`: transitive JSON files preserving identity, Requires-Python,
  Requires-Dist (including extras/markers), entry points and target tags.

Consumers can access the `metadata` and `runtime_tars` output groups. For OCI:

```starlark
load("@sonic_build_infra//python:source_package.bzl", "python_runtime_packages")
load("@rules_oci//oci:defs.bzl", "oci_image")

python_runtime_packages(name = "python_runtime", deps = [":runtime"])
filegroup(
    name = "python_runtime_metadata",
    srcs = [":python_runtime"],
    output_group = "metadata",
)
oci_image(name = "image", tars = [":python_runtime"])
```

The collector follows the **declared source target graph**, deduplicating diamond
dependencies. Image assembly must separately resolve requirements for its Python,
platform and selected extras, map all requirements to source producers or locked
third-party wheels, and check missing packages and version conflicts. Passing
`requires=[...]` alone does not locate a producer. The provider's package records
keep each payload associated with its metadata for that check. Third-party wheel
installation remains the image assembly layer's `wheel_layer` responsibility.
Native extension shared-library dependencies also belong to the image runtime
closure; this Python collector does not infer ELF dependencies.

## Validation

`//python/tests:site_packages_test` exercises the public installation, TAR and
runtime collector interfaces: installed imports/resources/entrypoints, wheel
metadata equivalence, RECORD hashes and three-level diamond dependency handoff.
`//python/tests:source_package_builder_test` covers explicit resources/extensions,
data schemes, target paths/interpreter, conditional extras and rejected inputs.

## Generate and consume a runtime lock

Load `python_runtime_plan` and `checked_python_runtime` from
`//python:runtime_lock.bzl`. The plan reads source metadata through
`PythonRuntimeInfo`, including transitive source producers. It contains the raw
metadata, source identities, root requirements/extras, target marker environment,
index configuration, constraints and fixed uv version. It never runs a resolver
and is independent of the reviewed runtime lock.

```starlark
python_runtime_plan(
    name = "python_plan",
    packages = [":runtime"],
    roots = ["example-service[metrics]"],
    source_distributions = ["example-service", "example-common"],
    python_version = "3.13",
    python_platform = "x86_64-unknown-linux-gnu",
    uv_version = "0.12.18",
)
```

`source_distributions` declares names that must retain a source producer even
when that producer is missing. Every supplied source identity is also treated
as source-built. Source version conflicts and missing declared source producers
fail; these names are never silently resolved from PyPI. A fully specified target
marker environment selects applicable extras and conditions. The external
requirements export is concrete for that environment; raw marker/constraint
spelling remains in the plan, so a raw change still invalidates the input record.
`marker_environment` supplies any additional target facts such as release markers;
the exporter never inherits those values from the execution host.

Build the plan first. Then invoke the public native Python CLI with a fixed uv
executable. The following owner paths and plan target are examples to substitute:

```sh
bazel build //image:python_plan
bazel run @sonic_build_infra//python:runtime_lock -- update \
  --plan "$PWD/bazel-bin/image/python_plan.plan.json" \
  --requirements "$PWD/image/requirements.in" \
  --lock "$PWD/image/requirements_lock.txt" \
  --inputs "$PWD/image/lock-inputs.json" \
  --uv /absolute/path/to/fixed/uv-0.12.18
```

The update command verifies uv's exact version, resolves with the old output lock
as the version-selection baseline, verifies exact versions and SHA256 hashes,
and publishes requirements, lock and input record only after successful
resolution. Index credentials belong in the execution environment, not the plan.
`--cache-dir` can select a writable resolver cache. Commit all three outputs with
the owner declarations. A declared `pip.parse` consumes `requirements_lock.txt`;
its `all_whl_requirements` export supplies the complete third-party wheel set to
`wheel_layer`.

The actual image/rootfs input is a checked runtime layer:

```starlark
checked_python_runtime(
    name = "python_runtime",
    plan = ":python_plan",
    requirements = "requirements.in",
    lock = "requirements_lock.txt",
    inputs = "lock-inputs.json",
    sources = [":runtime"],
    third_party = [":third_party_runtime"],
    update_command = "the owner's complete runtime_lock update command",
)
```

Replace `update_command` with the actual owner command. `sources` are source
installation targets/collectors; their full transitive TAR set is used. The
checker compares current plan, exported requests and reviewed lock hashes, then
checks installed distribution identities/versions, source dependency metadata
and the applicable dependency graph from the actual installed METADATA files. Source and third-party sets must match the active plan and
reviewed lock. The existing Distroless `flatten` rule combines their TARs with a
validation-produced empty TAR, making the freshness/inventory check a required
input to the final archive without copying source archives or reimplementing a
TAR merger. OCI and rootfs consume the same resulting runtime TAR.

Normal build actions never execute uv or mutate a lock. Missing/stale input
records, changed source metadata, modified lock files, missing installed packages
or wrong installed versions fail with `update_command`. The CLI `check` command
can also validate plan/requirements/lock/input consistency before installation;
it takes the same four input options as `update` and performs no mutation.

`//python/tests:runtime_lock_test` covers source graph and marker contracts.
`python/tests:runtime_lock_tests.bzl` exposes `runtime_lock_update_test(name, uv)`
for real resolver tests with a Bazel-managed fixed uv 0.12.18 executable; no
resolver behavior is mocked.
