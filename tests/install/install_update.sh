#!/usr/bin/env bash

set -Eeuo pipefail

# shellcheck source=tests/install/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

root="$(new_test_root)"
make_env "$root"
clear_runtime_env
prepare_update_install "$root"

printf 'dirty\n' >>"$UPDATE_INSTALL_DIR/active-gerrit/SKILL.md"

run_installer update
assert_status_ne 0
assert_contains "$LAST_STDERR" 'Working tree is dirty'

root="$(new_test_root)"
make_env "$root"
clear_runtime_env
prepare_doctor_install "$root"

run_installer doctor --install-dir "$DOCTOR_INSTALL_DIR" --config-file "$DOCTOR_CONFIG_FILE" --json
assert_status_eq 0
assert_contains "$LAST_STDOUT" '"source": "gerrit"'
assert_contains "$LAST_STDOUT" '"source": "workflow"'
assert_contains "$LAST_STDOUT" '"env_loaded": "1"'
assert_contains "$LAST_STDOUT" '"active_gerrit_home": '
assert_not_contains "$LAST_STDOUT" 'secret-token'

printf 'PASS %s\n' "${BASH_SOURCE[0]##*/}"