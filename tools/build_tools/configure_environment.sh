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
# GCC copies native -I arguments into COLLECT_GCC_OPTIONS before spawning cc1.
# Large CcInfo include/library closures can exceed Linux's per-string limit even
# when argv itself fits. Forward the native include group to the preprocessor;
# keep its precedence before existing forwarded options and leave linker search
# arguments unchanged. Materialize the launcher in this private configure tree
# so consumers still need only this declared adapter as build data.
sonic_driver="$PWD/.sonic-compiler-argv"
cat > "$sonic_driver" <<'SONIC_COMPILER_ARGV'
#!/bin/sh
set -eu
compiler=$1
shift
# Response files may contain native includes. Preserve their driver ordering
# rather than partially rewriting a group whose contents are not visible here.
for argument in "$@"; do
    case "$argument" in @*) exec "$compiler" "$@" ;; esac
done
takes_operand() {
    case "$1" in
        -Xpreprocessor|-Xlinker|-Xassembler|-o|-x|-include|-imacros|-iquote|-isystem|\
        -idirafter|-iprefix|-iwithprefix|-iwithprefixbefore|-isysroot|-imultilib|\
        -imultiarch|-D|-U|-A|-l|-L|-B|-MF|-MT|-MQ|-specs|-wrapper|--sysroot|\
        --param|-dumpbase|-dumpbase-ext|-dumpdir|-auxbase|-auxbase-strip|-iplugindir)
            return 0 ;;
    esac
    return 1
}
original_count=$#
pending=
# A for loop snapshots its words: appending converted arguments does not change
# the iteration. Original argv stays at the front until both passes finish.
for argument in "$@"; do
    case "$pending" in
        include) set -- "$@" -Xpreprocessor "-I$argument"; pending=; continue ;;
        operand) pending=; continue ;;
    esac
    case "$argument" in
        -I) pending=include ;;
        -I?*) set -- "$@" -Xpreprocessor "$argument" ;;
        *) if takes_operand "$argument"; then pending=operand; fi ;;
    esac
done
if test "$pending" = include; then
    echo 'Missing compiler argument after -I' >&2
    exit 2
fi
remaining=$original_count
pending=
for argument in "$@"; do
    test "$remaining" -gt 0 || break
    remaining=$((remaining - 1))
    case "$pending" in
        include) pending=; continue ;;
        operand) set -- "$@" "$argument"; pending=; continue ;;
    esac
    case "$argument" in
        -I) pending=include ;;
        -I?*) ;;
        *) set -- "$@" "$argument"
           if takes_operand "$argument"; then pending=operand; fi ;;
    esac
done
shift "$original_count"
exec "$compiler" "$@"
SONIC_COMPILER_ARGV
chmod +x "$sonic_driver"
CC="$sonic_driver $CC"
CXX="$sonic_driver $CXX"
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
