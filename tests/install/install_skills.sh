#!/usr/bin/env bash

set -Eeuo pipefail

# shellcheck source=tests/install/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

root="$(new_test_root)"
make_env "$root"
clear_runtime_env
prepare_skill_install "$root"

run_installer deploy-skill --install-dir "$SKILL_INSTALL_DIR" --skill-dir "$SKILL_TARGET_DIR" --skill-mode symlink --no-profile
assert_status_eq 0
assert_symlink_to "$SKILL_TARGET_DIR/active-gerrit" "$SKILL_INSTALL_DIR/active-gerrit"
assert_symlink_to "$SKILL_TARGET_DIR/active-gerrit-workflow" "$SKILL_INSTALL_DIR/active-gerrit-workflow"

root="$(new_test_root)"
make_env "$root"
clear_runtime_env
prepare_skill_install "$root"

run_installer deploy-skill --install-dir "$SKILL_INSTALL_DIR" --skill-dir "$SKILL_TARGET_DIR" --skill-mode copy --no-profile
assert_status_eq 0
assert_path_exists "$SKILL_TARGET_DIR/active-gerrit/SKILL.md"
assert_path_exists "$SKILL_TARGET_DIR/active-gerrit-workflow/SKILL.md"
assert_not_symlink "$SKILL_TARGET_DIR/active-gerrit"
assert_not_symlink "$SKILL_TARGET_DIR/active-gerrit-workflow"
assert_path_missing "$SKILL_TARGET_DIR/active-gerrit/__pycache__"
assert_path_missing "$SKILL_TARGET_DIR/active-gerrit-workflow/.cache"

root="$(new_test_root)"
make_env "$root"
clear_runtime_env
prepare_skill_install "$root"
mkdir -p -- "$SKILL_TARGET_DIR/active-gerrit"
printf 'user-owned\n' >"$SKILL_TARGET_DIR/active-gerrit/README.txt"

run_installer deploy-skill --install-dir "$SKILL_INSTALL_DIR" --skill-dir "$SKILL_TARGET_DIR" --skill-mode copy --no-profile
assert_status_ne 0
assert_contains "$LAST_STDERR" 'already exists and is not installer-managed'
assert_path_exists "$SKILL_TARGET_DIR/active-gerrit/README.txt"

printf 'PASS %s\n' "${BASH_SOURCE[0]##*/}"