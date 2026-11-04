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
# GCC copies native include arguments into COLLECT_GCC_OPTIONS before spawning cc1.
# Large CcInfo include/library closures can exceed Linux's per-string limit even
# when argv itself fits. Forward native -I, -iquote and -isystem options to the
# preprocessor without changing their search classes or order. Keep their
# precedence before existing forwarded options. Shorten absolute -L
# directories relative to the physical working directory without changing their
# order or forwarding them behind GCC's default library paths. Materialize the
# launcher in this private configure tree so consumers still need only this
# declared adapter as build data.
sonic_driver="$PWD/.sonic-compiler-argv"
cat > "$sonic_driver" <<'SONIC_COMPILER_ARGV'
#!/bin/sh
set -eu
compiler=$1
shift
# Only the owner-generated vector beside this launcher uses this transport.
# Ordinary GCC response files remain opaque. Expand before the existing -L
# shortening, so recursive Make/Libtool keeps its own working directory and
# GCC still receives library searches in their original driver order.
linker_response="${0%/*}/.sonic-linker-arguments"
remaining=$#
while test "$remaining" -gt 0; do
    argument=$1
    shift
    remaining=$((remaining - 1))
    if test "$argument" = "@$linker_response"; then
        {
            IFS= read -r response_format
            test "$response_format" = sonic-linker-arguments-v1 || {
                echo "Invalid private linker argument response" >&2
                exit 2
            }
            while IFS= read -r response_argument || test -n "$response_argument"; do
                set -- "$@" "$response_argument"
            done
        } < "$linker_response"
    else
        set -- "$@" "$argument"
    fi
done
# Response files may contain native includes. Preserve their driver ordering
# rather than partially rewriting a group whose contents are not visible here.
for argument in "$@"; do
    case "$argument" in @*) exec "$compiler" "$@" ;; esac
done
compiler_cwd=$(pwd -P)
short_library_path() {
    library_path=$1
    case "$library_path" in /*) ;; *) return ;; esac
    # Preserve special double-slash roots and paths containing parent/current
    # components. In particular, do not normalize '..' across a symlink.
    case "/${library_path#/}/" in *//*|*/../*|*/./*) return ;; esac
    library_base=$compiler_cwd
    library_prefix=
    while :; do
        case "$library_path" in
            "$library_base") library_relative=${library_prefix:-.}; break ;;
            "$library_base"/*)
                library_relative="$library_prefix${library_path#"$library_base"/}"; break ;;
        esac
        if test "$library_base" = /; then
            library_relative="$library_prefix${library_path#/}"
            break
        fi
        library_base=${library_base%/*}
        test -n "$library_base" || library_base=/
        library_prefix="../$library_prefix"
    done
    if test "${#library_relative}" -lt "${#library_path}"; then
        library_path=$library_relative
    fi
}
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
        -iquote|-isystem)
            set -- "$@" -Xpreprocessor "$pending" -Xpreprocessor "$argument"
            pending=; continue ;;
        operand) pending=; continue ;;
    esac
    case "$argument" in
        -I) pending=include ;;
        -iquote|-isystem) pending=$argument ;;
        -I?*|-iquote?*|-isystem?*) set -- "$@" -Xpreprocessor "$argument" ;;
        *) if takes_operand "$argument"; then pending=operand; fi ;;
    esac
done
test "$pending" != include || pending=-I
case "$pending" in
    -I|-iquote|-isystem)
        echo "Missing compiler argument after $pending" >&2
        exit 2 ;;
esac
remaining=$original_count
pending=
for argument in "$@"; do
    test "$remaining" -gt 0 || break
    remaining=$((remaining - 1))
    case "$pending" in
        include) pending=; continue ;;
        operand) set -- "$@" "$argument"; pending=; continue ;;
        library) short_library_path "$argument"
                 set -- "$@" "$library_path"; pending=; continue ;;
    esac
    case "$argument" in
        -I|-iquote|-isystem) pending=include ;;
        -I?*|-iquote?*|-isystem?*) ;;
        -L) set -- "$@" "$argument"; pending=library ;;
        -L?*) short_library_path "${argument#-L}"
              set -- "$@" "-L$library_path" ;;
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
