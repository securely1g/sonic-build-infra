#!/usr/bin/env bash
# Run the source-owned regression contract on a native Debian Trixie host.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
root=$PWD
artifacts="$root/ci-artifacts"
mkdir -p "$artifacts"
exec > >(tee "$artifacts/validation.log") 2>&1
python3 tools/rust/prepare_test.py
case "$(uname -m)" in
  x86_64) cpu=x86_64; debian_arch=amd64 ;;
  aarch64) cpu=aarch64; debian_arch=arm64 ;;
  *) echo 'Native AMD64 or ARM64 is required' >&2; exit 1 ;;
esac
[[ "$(dpkg --print-architecture)" == "$debian_arch" ]]
. /etc/os-release
[[ "$ID" == debian && "$VERSION_CODENAME" == trixie ]]
cp /etc/os-release "$artifacts/os-release"
uname -a > "$artifacts/uname.txt"
dpkg-query -W > "$artifacts/host-packages.txt"
git rev-parse HEAD HEAD^{tree} > "$artifacts/revisions.txt"
python3 apt/lock.py check | tee "$artifacts/apt-lock-check.log"
mkdir -p "$artifacts/apt" "$artifacts/ci"
cp apt/apt.lock.json apt/sources.json apt/requests.json "$artifacts/apt/"
cp ci/source_mirrors.cfg "$artifacts/ci/"
# Use the maintained SONiC registry by default.
# Record the effective endpoint with the generated module lock.
registry=${SONIC_BAZEL_REGISTRY:-https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main}
printf '%s\n' "$registry" > "$artifacts/registry.txt"
# Explicit CI transport must also reach the nested shared-API workspace.
# Ordinary local callers retain the checked-in mirror configuration.
downloader_config=${SONIC_BAZEL_DOWNLOADER_CONFIG:-$root/ci/source_mirrors.cfg}
if [[ "$downloader_config" != /* || ! -f "$downloader_config" || -L "$downloader_config" ]]; then
  echo 'Selected downloader configuration must be an absolute regular non-symlink file' >&2
  exit 1
fi
resolution=(--registry="$registry" --registry=https://bcr.bazel.build/ --lockfile_mode=update
            --downloader_config="$downloader_config")
platform="//platforms:${cpu}_trixie"
flags=("${resolution[@]}" --platforms="$platform" --host_platform="$platform" --jobs=4
       '--remote_download_regex=(^|.*/)tools/build_tools/kernel_runtime_(amd64|arm64)(/.*)?$')
bazel_cmd=(bazel --ignore_all_rc_files)
# Exercise cold repository setup and lock-change invalidation with queries only.
# The workflow runs this on both native AMD64 and ARM64 hosts.
python3 -B apt/tests/integration/inputs_repository_test.py --bazel bazel
tests=(
  //go:lock_test
  //deb:assemble_test
  //tests:hello_deb_optional_fields_build_test
  //deps:alternatives_test
  //oci/tests:runtime_packages_test //oci/tests:runtime_packages_rule_test
  //oci/tests:runtime_control_files_rule_test
  //oci/tests:installed_packages_test
  //oci/tests:owner_metadata_test //oci/tests:installed_packages_rule_test
  //oci/tests:installed_packages_alias_test
  //oci/tests:account_layer_test
  //apt:lock_test
  //apt:bookworm_lock_test
  //python/tests:runtime_lock_test
  //python/tests:source_package_builder_test
  //python/tests:site_packages_test
  //apt:selection_test
  //apt:inputs_test
  //apt:dependencies_test
  //apt/tests/integration:adapter_test
  //apt/tests/integration:static_link_test
  //apt/tests/integration:raw_archive_labels_test
  //apt/tests/integration:overlap_test
  //tests:hello_build_test
  //tests:hello_cpp_build_test
  //tests:hello_strip_test
  //tests:hello_rpath_preservation_test
  //tests:hello_runpath_preservation_test
  //tests:hello_static_preservation_test
  //tests:hello_rpath_exact_deployment_test
  //tests:hello_runpath_exact_deployment_test
  //tests:source_python_embed_test
  //tests:debug_compression_test
  //tools/autoconf:probe_runner_test
  //tools/autoconf:launcher_test
  //tools/autoconf:argv_paths_test
  //tools/autoconf/tests:declared_tools_test
  //tools/autoconf/tests:relocatable_pkgconfig_test
  //tools/build_tools:gdb_runtime_test
  //cc/tests/toolchain_variant:library_provider_test
  //cc/tests/toolchain_variant:shared_provider_test
  //cc/tests/toolchain_variant:symbols_provider_test
  //cc/tests/toolchain_variant:binary_runfiles_test
  //cc/tests/toolchain_variant:imported_shared_consumer_test
  //tools/autoconf:linker_response_test
  //tests/pkgconfig:repeated_dynamic_test
  //tests/pkgconfig:mixed_first_test
  //tests/pkgconfig:mixed_second_test
  //tests/pkgconfig:unsupported_includes_test
  //tests/debug_control:all
  //tests:cc_toolchain_supports_pic_test
  //tests:libgreet_build_test
  //tests:libgreet_pic_test
  //tests:libgreet_strip_test
  //tests:greet_shared_build_test
  //tests:greet_shared_pic_test
  //tests:greet_shared_strip_test
  //tests:hello_deploy_tar_content_test
  //tests:hello_deploy_tar_provides_debug_symbols_test
  //proto:protoc_version_test
  //python:py_native_library_test
  //swig/tests:defines_test
  //swig:extract_templates_test
  //tools/build_tools:image_runtime_test
  //tools/build_tools:manual_runtime_test
  //tests:deploy_tar_timestamps_test
  //toolchains/gcc/tests:foreign_link_test
  //cc/tests:transitive_runtime_test
  //cc/tests:shared_library_layout_test
  //cc/tests/runtime_context:layout_test
  //cc/tests/runtime_context:paired_layout_test
  //cc/tests/runtime_context:paired_flags_test
  //cc/tests/runtime_context:plain_dynamic_deps_test
  //cc/tests/runtime_context:dynamic_deps_layout_test
  //cc/tests/runtime_context:paired_owners_test
  //cc/tests/runtime_context:plain_owners_test
  //cc/tests/runtime_test:native_test
  //cc/tests/runtime_test:preparation_test
  //cc/tests/runtime_test:contract_test
  //cc/tests/runtime_test:origin_contract_test
  //testing:exec_test_args_test
  //tests:hello_rpath_installation_test
  //tests:hello_runpath_installation_test
  //tests:installation_rpath_tar_test
  //toolchains/gcc/tests:prefix_map_test
)
# These feature-specific targets are explicit; adding either public rule family
# includes its existing regression tests in this shared workflow.
if [[ -f python/wheel_layer.bzl ]]; then
  tests+=(//python:wheel_layer_test)
fi
if [[ -f tar/root_owned_tar.bzl ]]; then
  tests+=(//tar:root_owned_tar_test //tar:debug_symbols_ownership_test //tar:debug_symbols_modes_test)
fi
if [[ -f tools/build_tools/prepare_rootfs.py ]]; then
  tests+=(//tools/build_tools:prepare_rootfs_test //tools/build_tools:kernel_debian_test
    //tools/build_tools:kernel_runtime_test)
  if [[ "$debian_arch" == amd64 ]]; then
    tests+=(//tools/build_tools:kernel_cross_runtime_arm64_test //tools/build_tools:kernel_cross_runtime_armhf_test)
  fi
fi
outputs=(//tests:hello //tests:hello_cpp //tests:hello_deploy_tar //tests:hello_deploy_tar.debug_symbols
  //tar:timestamp_default_tar //tests:timestamp_deploy_tar //tests:timestamp_deploy_tar.debug_symbols)
if [[ "$debian_arch" == amd64 ]]; then
  tests+=(//tools/build_tools:installer_worker_debs_test)
fi
if [[ "$debian_arch" == amd64 && -f tools/build_tools/docbook_runtime_test.py ]]; then
  tests+=(//tools/build_tools:docbook_runtime_test)
fi
# Audit the actual configured graph, including imported tests, before executing
# any action. The dedicated DEB compatibility test intentionally creates DEBs;
# keep it in the test suite and audit the ordinary production graph separately.
printf '%s\n' "${tests[@]}" "${outputs[@]}" > "$artifacts/targets.txt"
audit_tests=()
for target in "${tests[@]}"; do
  if [[ "$target" != //tests:hello_deb_optional_fields_build_test ]]; then
    audit_tests+=("$target")
  fi
done
"${bazel_cmd[@]}" aquery "${flags[@]}" --output=jsonproto \
  "deps(set(${audit_tests[*]} ${outputs[*]}))" > "$artifacts/actions.json"
apt_python_tests=(//apt:dependencies_test //apt:selection_test //apt:inputs_test //apt/tests/integration:adapter_test //apt/tests/integration:overlap_test)
apt_python_flags=("${flags[@]}" --@rules_python//python/config_settings:python_version=3.13)
"${bazel_cmd[@]}" aquery "${apt_python_flags[@]}" --output=jsonproto \
  "deps(set(${apt_python_tests[*]}))" > "$artifacts/apt-python313-actions.json"
# Explicitly exercise the individual APT library/header exports on Bookworm.
# The same native CPU uses Trixie execution tools and a Bookworm target ABI.
bookworm_tests=(//cc/tests/header_includes:headers_link_test
  //toolchains/bookworm/tests:abi_test //toolchains/bookworm/tests:native_test
  //apt/tests/integration:bookworm_library_import_test)
bookworm_flags=("${flags[@]}" --platforms="//platforms:${cpu}_bookworm")
printf '%s\n' "${bookworm_tests[@]}" > "$artifacts/bookworm-targets.txt"
"${bazel_cmd[@]}" aquery "${bookworm_flags[@]}" --output=jsonproto \
  "deps(set(${bookworm_tests[*]}))" > "$artifacts/bookworm-actions.json"
python3 - "$artifacts" <<'PYTHON'
import collections, json, re, sys
from pathlib import Path
root = Path(sys.argv[1])
for filename in ('actions.json', 'apt-python313-actions.json', 'bookworm-actions.json'):
    graph = json.loads((root / filename).read_text())
    fragments = {item['id']: item for item in graph['pathFragments']}
    def path(identifier):
        node = fragments[identifier]
        return (path(node['parentId']) + '/' if node.get('parentId') else '') + node['label']
    artifacts = {item['id']: path(item['pathFragmentId']) for item in graph['artifacts']}
    actions = graph['actions']
    assert actions, 'empty action graph'
    outputs = [artifacts[i] for action in actions for i in action.get('outputIds', [])]
    forbidden = []
    for action in actions:
        command = ' '.join(action.get('arguments', []))
        if (re.search(r'(?:^|[/\s])(?:make|gmake|dpkg-buildpackage)(?:\s|$)', command) or
            ('dpkg-deb' in command and re.search(r'(?:--build|(?:^|\s)-b(?:\s|$))', command))):
            forbidden.append(action.get('mnemonic'))
    receipt = dict(action_count=len(actions),
                   mnemonics=dict(collections.Counter(item['mnemonic'] for item in actions)),
                   deb_outputs=[name for name in outputs if name.endswith('.deb')],
                   packaging_wrappers=forbidden)
    (root / filename.replace('actions.json', 'action-audit.json')).write_text(json.dumps(receipt, indent=2) + '\n')
    assert not receipt['deb_outputs'] and not forbidden, receipt
PYTHON
"${bazel_cmd[@]}" test "${flags[@]}" --nocache_test_results --test_output=errors \
  --build_event_json_file="$artifacts/tests.bep.json" "${tests[@]}"
# Include external test labels and their canonical repository paths in artifacts.
cp -aL bazel-testlogs "$artifacts/testlogs"
# The image consumer runs Python 3.13; retain its separate hub/runfiles proof.
"${bazel_cmd[@]}" test "${apt_python_flags[@]}" --nocache_test_results --test_output=errors \
  --build_event_json_file="$artifacts/apt-python313-tests.bep.json" "${apt_python_tests[@]}"
cp -aL bazel-testlogs "$artifacts/apt-python313-testlogs"
"${bazel_cmd[@]}" test "${bookworm_flags[@]}" --nocache_test_results --test_output=errors \
  --build_event_json_file="$artifacts/bookworm-tests.bep.json" "${bookworm_tests[@]}"
cp -aL bazel-testlogs "$artifacts/bookworm-testlogs"
"${bazel_cmd[@]}" build "${flags[@]}" --build_event_json_file="$artifacts/build.bep.json" "${outputs[@]}"
./bazel-bin/tests/hello
./bazel-bin/tests/hello_cpp
mkdir -p "$artifacts/outputs"
declare -A output_paths
for target in "${outputs[@]}"; do
  mapfile -t files < <("${bazel_cmd[@]}" cquery "${flags[@]}" --output=files "config($target, target)")
  printf '%s output: %s\n' "$target" "${files[*]}"
  [[ "${#files[@]}" -eq 1 && -f "${files[0]}" ]]
  cp "${files[0]}" "$artifacts/outputs/"
  output_paths["$target"]="$artifacts/outputs/$(basename "${files[0]}")"
done
python3 ci/verify_deploy_archives.py \
  --runtime "${output_paths[//tests:hello_deploy_tar]}" \
  --debug "${output_paths[//tests:hello_deploy_tar.debug_symbols]}" \
  --source-data tests/testdata/file.txt --architecture "$debian_arch" \
  > "$artifacts/deploy-archives.json"
"${bazel_cmd[@]}" mod graph "${resolution[@]}" > "$artifacts/module-graph.txt"
cp MODULE.bazel MODULE.bazel.lock python/wheel_requirements_lock.txt "$artifacts/"
if [[ -f tests/shared_api_consumer/MODULE.bazel ]]; then
  (
    cd tests/shared_api_consumer
    fixture_tests=(//:sysroot_test)
    for language in python go; do
      for width in 32 64; do
        for layout in files tree; do
          fixture_tests+=("//:${language}_${width}_${layout}_test")
        done
      done
    done
    "${bazel_cmd[@]}" test "${resolution[@]}" --jobs=4 --nocache_test_results --test_output=errors \
      --build_event_json_file="$artifacts/shared-api.bep.json" "${fixture_tests[@]}"
    if "${bazel_cmd[@]}" query "${resolution[@]}" @unsafe//:files > "$artifacts/unsafe-sysroot.log" 2>&1; then
      echo 'Unsafe archive was unexpectedly accepted' >&2; exit 1
    fi
    grep -F 'The Debian sysroot contains unexpected absolute symlinks' "$artifacts/unsafe-sysroot.log"
    cp MODULE.bazel.lock "$artifacts/shared-api.MODULE.bazel.lock"
    for target in "${fixture_tests[@]}"; do
      name="${target#//:}"
      mkdir -p "$artifacts/tests/shared-api/$name"
      cp "bazel-testlogs/$name/test.xml" "bazel-testlogs/$name/test.log" "$artifacts/tests/shared-api/$name/"
    done
  )
fi
python3 - "$artifacts" "$platform" <<'PYTHON'
import hashlib, json, os, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'validation.log'}
record = dict(revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              platform=sys.argv[2], image=os.environ.get('CI_IMAGE'),
              machine=os.uname().machine,
              bazel=subprocess.check_output(['bazel', '--version'], text=True).strip(),
              github_run_id=os.environ.get('GITHUB_RUN_ID'), sha256=files)
(root / 'provenance.json').write_text(json.dumps(record, indent=2) + '\n')
PYTHON
