#!/usr/bin/env bash

set -Eeuo pipefail

# shellcheck source=tests/install/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

root="$(new_test_root)"
make_env "$root"
clear_runtime_env

install_dir="$XDG_DATA_HOME/active-gerrit-workflow"
mkdir -p -- "$install_dir"
export NONINTERACTIVE=1

run_installer config --install-dir "$install_dir"
assert_status_ne 0
assert_contains "$LAST_STDERR" 'NONINTERACTIVE=1 requires GERRIT_BASE_URL'

root="$(new_test_root)"
make_env "$root"
clear_runtime_env

install_dir="$XDG_DATA_HOME/active-gerrit-workflow"
mkdir -p -- "$install_dir"
export NONINTERACTIVE=1
export GERRIT_BASE_URL='https://gerrit.example.com'
export GERRIT_USERNAME='ci-user'
export GERRIT_HTTP_PASSWORD='ci-secret'
export GERRIT_TIMEOUT_SECONDS='60'

run_installer config --install-dir "$install_dir"
assert_status_eq 0
assert_contains "$LAST_STDOUT" 'GERRIT_HTTP_PASSWORD=<redacted>'
assert_not_contains "$LAST_STDOUT" 'ci-secret'

config_file="$XDG_CONFIG_HOME/active-gerrit-workflow/env"
assert_path_exists "$config_file"
assert_file_mode "$config_file" '600'
assert_contains "$(cat -- "$config_file")" 'export GERRIT_USERNAME=ci-user'
assert_contains "$(cat -- "$config_file")" 'export GERRIT_HTTP_PASSWORD=ci-secret'

printf 'PASS %s\n' "${BASH_SOURCE[0]##*/}"