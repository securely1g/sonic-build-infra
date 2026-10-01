#!/usr/bin/env bash
set -euo pipefail
assembled=$(dirname "$1")
reversed=$(dirname "$2")
test "$(cat "$assembled/usr/include/order.h")" = second
test "$(cat "$reversed/usr/include/order.h")" = first
for directory in bin lib sbin; do
    test "$(cat "$assembled/$directory/payload")" = second
    test "$(cat "$reversed/$directory/payload")" = first
done
