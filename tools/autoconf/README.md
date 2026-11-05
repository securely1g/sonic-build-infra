# Controlled Autoconf inspection

`autoconf_probe` uses the selected target C/C++ toolchain and declared dependency
`CcInfo`/`.pc` files. `execution_runtime` defaults to the existing
`//tools/build_tools:foreign_runtime` and is resolved in the execution configuration.
A temporary migration probe can supply a managed runtime containing the original
configure-time Python modules without using the host environment.

NM, OBJCOPY, STRIP, OBJDUMP and LD come from the same target toolchain as CC/AR.
OBJCOPY/STRIP use the configured action mapping. Legacy placeholder paths for
the remaining tools are resolved with that selected compiler’s `-print-prog-name`
and must belong to its declared `all_files`; an ambient PATH lookup is rejected.
The probe exports launchers explicitly so libtool cannot fall back to `/bin/nm`.
Their actual commands are retained in the probe identity and command reports.

`pkg_config_fdo_sysroot_rules` defaults to `False`. Opt in only when a declared
producer supplies an execution-root relocatable build `.pc` together with APT
system `.pc` files: pkgconf then keeps non-system producer paths and relocates
system prefixes through the selected sysroot. This does not add search paths.
Every emitted `-I`, `-L`, `-isystem`, `-iquote`, `-idirafter` and absolute library
operand must be accessible through a declared input file/tree. Existing host
paths alone are rejected. Queries are retained in `pkg-config.json`.

Compiler launchers retain configure's working directory and all declared include
paths. Declared paths below the execution root use relative compiler arguments,
so long sandbox prefixes do not inflate GCC's COLLECT_GCC_OPTIONS. Consecutive
pure linker operands use GNU ld response files without crossing driver options
or source/object inputs; compiler options and library order remain intact.
`launcher_test` checks actual compilation, preprocessing, assembly, output paths
and spaces with a long include list. `linker_response_test` checks actual ordered
static linking, including archive paths with spaces.
