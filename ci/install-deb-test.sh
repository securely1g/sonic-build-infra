#!/usr/bin/env bash
# Run only in a disposable native Debian container: installs the test packages.
set -euo pipefail
packages=$1
evidence=$2
exec > >(tee "$evidence") 2>&1
[[ "$EUID" -eq 0 ]]
arch=$(dpkg --print-architecture)
runtime="$packages/sonic-infra-hello_1.0_${arch}.deb"
symbols="$packages/sonic-infra-hello-dbg_1.0_${arch}.deb"
[[ -f "$runtime" && -f "$symbols" ]]
dpkg --install "$runtime" "$symbols"
[[ "$(dpkg-query -W -f='${Status}' sonic-infra-hello)" == 'install ok installed' ]]
[[ "$(dpkg-query -W -f='${Status}' sonic-infra-hello-dbg)" == 'install ok installed' ]]
dpkg --verify sonic-infra-hello sonic-infra-hello-dbg
/usr/bin/hello
build_id=$(readelf -n /usr/bin/hello | sed -n 's/.*Build ID: //p')
[[ -n "$build_id" ]]
debug="/usr/lib/debug/.build-id/${build_id:0:2}/${build_id:2}.debug"
[[ -f "$debug" ]]
[[ "$(readelf -n "$debug" | sed -n 's/.*Build ID: //p')" == "$build_id" ]]
readelf --debug-dump=decodedline "$debug" > "${evidence%.txt}-lines.txt"
grep -q 'hello.c' "${evidence%.txt}-lines.txt"
printf 'Installed runtime and matching debug symbols: %s (%s)\n' "$build_id" "$arch"
