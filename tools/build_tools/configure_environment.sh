#!/bin/sh
# Adapt rules_foreign_cc's compiler environment for upstream Autoconf/Libtool.
# Modern rules_cc describes link actions without a legacy ld tool path. Ask the
# selected GCC driver for its declared binutils, never search the host PATH.
set -eu
: "${CC:?configure requires the selected C compiler}"
: "${CXX:?configure requires the selected C++ compiler}"
: "${EXT_BUILD_ROOT:?configure requires the Bazel execution root}"
LD=$("$CC" -print-prog-name=ld)
case "$LD" in
    "$EXT_BUILD_ROOT"/*) test -x "$LD" ;;
    *) echo "Selected GCC did not return a declared linker: $LD" >&2; exit 1 ;;
esac
# Autoconf does not apply CFLAGS/CXXFLAGS to preprocessor probes. Preserve the
# declared sysroot and include options so those probes cannot select /lib/cpp.
# Libtool removes -B options from C link flags. Keeping the joined search
# arguments in the compiler command preserves the declared linker/startup paths.
for option in ${CFLAGS-}; do
    case "$option" in -B?*) CC="$CC $option" ;; esac
done
for option in ${CXXFLAGS-}; do
    case "$option" in -B?*) CXX="$CXX $option" ;; esac
done
CPP="$CC ${CFLAGS-} -E"
CXXCPP="$CXX ${CXXFLAGS-} -E"
export CC CXX LD CPP CXXCPP
exec "$@"
