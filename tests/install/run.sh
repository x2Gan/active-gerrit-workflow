#!/usr/bin/env bash

set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$TEST_DIR/../.." && pwd)"

run_step() {
  printf '==> %s\n' "$*"
  "$@"
}

cd -- "$REPO_ROOT"

run_step bash -n install.sh "$TEST_DIR"/*.sh

if command -v shellcheck >/dev/null 2>&1; then
  run_step shellcheck -x install.sh "$TEST_DIR"/*.sh
else
  printf '==> shellcheck not found, skipping static shell lint\n'
fi

run_step bash "$TEST_DIR/install_args.sh"
run_step bash "$TEST_DIR/install_config.sh"
run_step bash "$TEST_DIR/install_skills.sh"
run_step bash "$TEST_DIR/install_update.sh"
run_step python -m unittest tests.test_install_sh

printf 'Installer test suite passed.\n'