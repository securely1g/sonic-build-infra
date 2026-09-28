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

# Drop DT_RPATH and DT_RUNPATH before the split, so neither half carries them.
if [[ "$strip_rpaths" == "true" ]]; then
  "$patchelf" --remove-rpath "$tmp"
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
