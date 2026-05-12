#!/usr/bin/env bash

set -Eeuo pipefail

# shellcheck source=tests/install/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

root="$(new_test_root)"
make_env "$root"
clear_runtime_env

run_installer help
assert_status_eq 0
assert_contains "$LAST_STDOUT" 'Usage:'
assert_eq '' "$LAST_STDERR"

run_installer --definitely-unknown
assert_status_ne 0
assert_contains "$LAST_STDERR" 'Unknown option: --definitely-unknown'
assert_contains "$LAST_STDERR" "Run \`install.sh --help\` for usage."

printf 'PASS %s\n' "${BASH_SOURCE[0]##*/}"