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
# Retain generated module resolution as evidence. The Distroless patches are
# checked into this repository; dependency registration comes from main.
registry=https://raw.githubusercontent.com/securely1g/sonic-bazel-registry/main
printf '%s\n' "$registry" > "$artifacts/registry.txt"
resolution=(--registry="$registry" --registry=https://bcr.bazel.build/ --lockfile_mode=update)
platform="//platforms:${cpu}_trixie"
# Upstream 0.9.4 has optional archive globs in its shared test BUILD file.
flags=("${resolution[@]}" --platforms="$platform" --host_platform="$platform" --jobs=4
  --noincompatible_disallow_empty_glob)
bazel_cmd=(bazel --ignore_all_rc_files)
tests=(
  //apt:selection_test
  //third_party/rules_distroless/tests:protobuf_headers_test
  @rules_distroless//apt/tests:locked_closure_test
  @rules_distroless//apt/tests:foreign_dependency_test
  @rules_distroless//apt/tests:foreign_all_key_test
  @rules_distroless//apt/tests:native_package_all_key_test
  @rules_distroless//apt/tests:missing_dependency_test
  @rules_distroless//apt/tests:conflicting_version_test
  @rules_distroless//apt/tests:missing_source_hash_test
  @rules_distroless//apt/tests:partial_content_hash_test
  @rules_distroless//apt/tests:metadata_mismatch_test
  @rules_distroless//apt/tests:locked_import_test
  @rules_distroless//distroless/tests:flatten/merge_simple
  @rules_distroless//distroless/tests:flatten/deduplicate
  @rules_distroless//distroless/tests:flatten/gzip_compression
  @rules_distroless//distroless/tests:flatten_manifest_test
  //tests:hello_build_test
  //tests:hello_cpp_build_test
  //tests:hello_strip_test
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
  //tests:deploy_tar_timestamps_test
)
# These feature-specific targets are explicit; adding either public rule family
# includes its existing regression tests in this shared workflow.
if [[ -f python/wheel_layer.bzl ]]; then
  tests+=(//python:wheel_layer_test)
fi
if [[ -f tar/root_owned_tar.bzl ]]; then
  tests+=(//tar:root_owned_tar_test //tar:debug_symbols_ownership_test)
fi
outputs=(//tests:hello //tests:hello_cpp //tests:hello_deploy_tar //tests:hello_deploy_tar.debug_symbols
  //tar:timestamp_default_tar //tests:timestamp_deploy_tar //tests:timestamp_deploy_tar.debug_symbols)
negative=(
  @rules_distroless//distroless/tests:flatten_manifest_unknown
  @rules_distroless//distroless/tests:flatten_manifest_duplicate
  @rules_distroless//distroless/tests:flatten_manifest_blank
)
# Audit the actual configured graph, including imported tests, before executing
# any action. Importing Debian archives is expected; producing DEBs is not.
printf '%s\n' "${tests[@]}" "${outputs[@]}" "${negative[@]}" > "$artifacts/targets.txt"
"${bazel_cmd[@]}" aquery "${flags[@]}" --output=jsonproto \
  "deps(set(${tests[*]} ${outputs[*]} ${negative[*]}))" > "$artifacts/actions.json"
python3 - "$artifacts" <<'PYTHON'
import collections, json, re, sys
from pathlib import Path
root = Path(sys.argv[1])
graph = json.loads((root / 'actions.json').read_text())
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
(root / 'action-audit.json').write_text(json.dumps(receipt, indent=2) + '\n')
assert not receipt['deb_outputs'] and not forbidden, receipt
PYTHON
"${bazel_cmd[@]}" test "${flags[@]}" --test_output=errors \
  --build_event_json_file="$artifacts/tests.bep.json" "${tests[@]}"
# Include external test labels and their canonical repository paths in artifacts.
cp -aL bazel-testlogs "$artifacts/testlogs"
for target in "${negative[@]}"; do
  name=${target##*:}
  if "${bazel_cmd[@]}" build "${flags[@]}" "$target" > "$artifacts/$name.log" 2>&1; then
    echo "Expected manifest rejection: $target" >&2
    exit 1
  fi
  if [[ "$name" == *duplicate ]]; then
    grep -F 'tar_manifest contains a duplicate tar:' "$artifacts/$name.log"
  else
    grep -F 'tar_manifest contains an undeclared tar:' "$artifacts/$name.log"
  fi
done
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
cp MODULE.bazel MODULE.bazel.lock "$artifacts/"
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
    "${bazel_cmd[@]}" test "${resolution[@]}" --jobs=4 --test_output=errors \
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
