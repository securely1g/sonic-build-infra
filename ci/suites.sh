# Shared source/declared-tool CI selection. Source this before invoking Bazel.
# Package generation is an explicit validation scope, including fixture DEBs.
ci_suite=${SONIC_BAZEL_CI_SUITE:-tar-runtime}
deb_fixture_tests=()
case "$ci_suite" in
  tar-runtime)
    kernel_runtime_test=//tools/build_tools:kernel_runtime_tools_test
    ;;
  deb-fixtures)
    kernel_runtime_test=//tools/build_tools:kernel_runtime_test
    deb_fixture_tests=(//tests:hello_deb_optional_fields_build_test //deb/tests:archive_format_test)
    ;;
  *)
    echo "Unknown SONIC_BAZEL_CI_SUITE: $ci_suite (expected tar-runtime or deb-fixtures)" >&2
    return 1
    ;;
esac

record_ci_suite() {
  local destination=$1
  shift
  python3 - "$ci_suite" "$destination" "$kernel_runtime_test" "$@" <<'PYTHON'
import json, sys
from pathlib import Path
suite, destination, kernel, *fixtures = sys.argv[1:]
record = {"suite": suite, "kernel_runtime_test": kernel,
          "deb_fixture_targets": [kernel, *fixtures] if suite == "deb-fixtures" else [],
          "deb_fixtures": "selected" if suite == "deb-fixtures" else "not requested"}
Path(destination).write_text(json.dumps(record, indent=2) + "\n")
PYTHON
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf 'CI suite: `%s`. DEB fixture generation: %s.\n' "$ci_suite" \
      "$([[ "$ci_suite" == deb-fixtures ]] && echo selected || echo 'not requested')" >> "$GITHUB_STEP_SUMMARY"
  fi
}
