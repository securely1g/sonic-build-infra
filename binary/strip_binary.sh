#!/bin/bash
#
# Splits one ELF into a stripped binary plus a build-id-named .debug file,
# mirroring Debian's dh_strip.
set -euo pipefail

input="$1"
stripped_out="$2"
debug_dir="$3"
objcopy="$4"
readelf="$5"
patchelf="$6"
strip_rpaths="${STRIP_RPATH:-false}"

# objcopy rewrites in place, so work on a writable copy kept inside the action's
# output tree (the input and /tmp may be read-only under sandboxing).
tmp="${stripped_out}.input_copy.tmp"
cp "$input" "$tmp"
chmod u+w "$tmp" # Make the copy writable for patchelf

# Edit the linked copy before the split, so runtime and debug artifacts match.
# Keep compiler features intact: LLVM may require runtime search directories.
dynamic="$("$readelf" -d "$tmp")"
# A static ELF has no dynamic section. Even dynamic ELFs may have no rpath;
# patchelf is unnecessary in both cases and cannot edit a static executable.
if [[ "$dynamic" == *"(RPATH)"* || "$dynamic" == *"(RUNPATH)"* ]]; then
  if [[ "$strip_rpaths" == "true" ]]; then
    "$patchelf" --remove-rpath "$tmp"
  else
    # Bazel-built images may need intentional deployment paths. Remove only
    # entries into Bazel's solib/runfiles trees, retaining order and empty entries.
    remaining="$("$patchelf" --print-rpath "$tmp")"
    kept=()
    changed=false
    while true; do
      entry="${remaining%%:*}"
      case "$entry" in
        _solib_*|*/_solib_*|*.runfiles|*.runfiles/*) changed=true ;;
        *) kept+=("$entry") ;;
      esac
      [[ "$remaining" == *:* ]] || break
      remaining="${remaining#*:}"
    done
    if [[ "$changed" == "true" ]]; then
      if [[ "${#kept[@]}" == 0 ]]; then
        "$patchelf" --remove-rpath "$tmp"
      else
        # patchelf otherwise upgrades DT_RPATH to DT_RUNPATH, changing precedence.
        rpath_flags=()
        if [[ "$dynamic" == *"(RPATH)"* && "$dynamic" != *"(RUNPATH)"* ]]; then
          rpath_flags+=(--force-rpath)
        fi
        filtered="$(IFS=:; printf '%s' "${kept[*]}")"
        "$patchelf" "${rpath_flags[@]}" --set-rpath "$filtered" "$tmp"
      fi
    fi
  fi
fi

# The .debug file is named by the binary's build-id; that is how gdb re-finds it
# via the .gnu_debuglink. The build-id is only known here, at action time.
build_id="$("$readelf" -n "$tmp" | awk '/Build ID:/ { print $NF }')"
if [[ -z "$build_id" ]]; then
  echo "ERROR: input binary '$input' has no build-id; cannot name the debug file" >&2
  rm -f "$tmp"
  exit 1
fi

# Debian's build-id layout: .build-id/NN/REST.debug, where
#  - NN = first two hex chars,
#  - REST = the remainder.
debug_file="${debug_dir}/.build-id/${build_id:0:2}/${build_id:2}.debug"
mkdir -p "$(dirname "$debug_file")"

# Extract the debug info into the build-id-named file.
"$objcopy" --only-keep-debug "$tmp" "$debug_file"

# Strip the debug info from the binary and point it back at the debug file.
# objcopy records only the basename in the .gnu_debuglink.
"$objcopy" --strip-debug --strip-unneeded --add-gnu-debuglink="$debug_file" "$tmp" "$stripped_out"

rm -f "$tmp"
