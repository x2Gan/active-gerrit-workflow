#!/usr/bin/env bash

if [[ -n "${ACTIVE_GERRIT_INSTALL_TEST_LIB_SOURCED:-}" ]]; then
  return 0
fi
ACTIVE_GERRIT_INSTALL_TEST_LIB_SOURCED=1

TEST_LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TEST_REPO_ROOT="$(cd -- "$TEST_LIB_DIR/../.." && pwd)"
INSTALLER_PATH="$TEST_REPO_ROOT/install.sh"
ORIGINAL_PATH="${PATH:-}"

TEST_TMP_DIRS=()
LAST_STATUS=0
LAST_STDOUT=""
LAST_STDERR=""

cleanup_test_dirs() {
  local path=""
  for path in "${TEST_TMP_DIRS[@]}"; do
    [[ -d "$path" ]] && rm -rf -- "$path"
  done
}

trap cleanup_test_dirs EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

new_test_root() {
  local root=""
  root="$(mktemp -d "${TMPDIR:-/tmp}/active-gerrit-install.XXXXXX")"
  TEST_TMP_DIRS+=("$root")
  printf '%s\n' "$root"
}

make_env() {
  local root="${1:?root is required}"

  export HOME="$root/home"
  export XDG_DATA_HOME="$root/xdg-data"
  export XDG_CONFIG_HOME="$root/xdg-config"
  export XDG_CACHE_HOME="$root/xdg-cache"
  export XDG_STATE_HOME="$root/xdg-state"
  export CODEX_HOME="$root/codex-home"
  export PATH="$ORIGINAL_PATH"
  export CI=1

  mkdir -p -- "$HOME" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$CODEX_HOME"
}

clear_runtime_env() {
  unset NONINTERACTIVE
  unset GERRIT_BASE_URL
  unset GERRIT_AUTH_TYPE
  unset GERRIT_USERNAME
  unset GERRIT_HTTP_PASSWORD
  unset GERRIT_VERIFY_SSL
  unset GERRIT_TIMEOUT_SECONDS
  unset GERRIT_DEFAULT_NOTIFY
  unset GERRIT_CACHE_DIR
}

run_installer() {
  local stdout_file=""
  local stderr_file=""

  stdout_file="$(mktemp "${TMPDIR:-/tmp}/active-gerrit-stdout.XXXXXX")"
  stderr_file="$(mktemp "${TMPDIR:-/tmp}/active-gerrit-stderr.XXXXXX")"

  if bash "$INSTALLER_PATH" "$@" >"$stdout_file" 2>"$stderr_file"; then
    LAST_STATUS=0
  else
    LAST_STATUS=$?
  fi

  LAST_STDOUT="$(cat -- "$stdout_file")"
  LAST_STDERR="$(cat -- "$stderr_file")"
  rm -f -- "$stdout_file" "$stderr_file"
}

assert_status_eq() {
  local expected="${1:?expected status is required}"
  [[ "$LAST_STATUS" -eq "$expected" ]] || fail "expected exit $expected, got $LAST_STATUS; stdout=$LAST_STDOUT stderr=$LAST_STDERR"
}

assert_status_ne() {
  local unexpected="${1:?unexpected status is required}"
  [[ "$LAST_STATUS" -ne "$unexpected" ]] || fail "did not expect exit $unexpected; stdout=$LAST_STDOUT stderr=$LAST_STDERR"
}

assert_contains() {
  local haystack="${1:-}"
  local needle="${2:?needle is required}"
  [[ "$haystack" == *"$needle"* ]] || fail "expected output to contain: $needle"
}

assert_not_contains() {
  local haystack="${1:-}"
  local needle="${2:?needle is required}"
  [[ "$haystack" != *"$needle"* ]] || fail "did not expect output to contain: $needle"
}

assert_eq() {
  local expected="${1:-}"
  local actual="${2:-}"
  [[ "$expected" == "$actual" ]] || fail "expected [$expected], got [$actual]"
}

assert_path_exists() {
  local path="${1:?path is required}"
  [[ -e "$path" ]] || fail "expected path to exist: $path"
}

assert_path_missing() {
  local path="${1:?path is required}"
  [[ ! -e "$path" ]] || fail "expected path to be absent: $path"
}

assert_not_symlink() {
  local path="${1:?path is required}"
  [[ ! -L "$path" ]] || fail "expected path not to be a symlink: $path"
}

assert_symlink_to() {
  local path="${1:?path is required}"
  local expected="${2:?expected target is required}"
  local actual_target=""
  local expected_target=""

  [[ -L "$path" ]] || fail "expected path to be a symlink: $path"
  actual_target="$(readlink -f -- "$path")"
  expected_target="$(readlink -f -- "$expected")"
  [[ "$actual_target" == "$expected_target" ]] || fail "expected $path -> $expected_target, got $actual_target"
}

assert_file_mode() {
  local path="${1:?path is required}"
  local expected_mode="${2:?expected mode is required}"
  local actual_mode=""

  actual_mode="$(stat -c '%a' "$path")"
  [[ "$actual_mode" == "$expected_mode" ]] || fail "expected mode $expected_mode for $path, got $actual_mode"
}

prepare_skill_install() {
  local root="${1:?root is required}"

  SKILL_INSTALL_DIR="$root/xdg-data/active-gerrit-workflow"
  # shellcheck disable=SC2034
  SKILL_TARGET_DIR="$root/codex-home/skills"

  mkdir -p -- \
    "$SKILL_INSTALL_DIR/active-gerrit/agents" \
    "$SKILL_INSTALL_DIR/active-gerrit/references" \
    "$SKILL_INSTALL_DIR/active-gerrit/scripts" \
    "$SKILL_INSTALL_DIR/active-gerrit-workflow/agents" \
    "$SKILL_INSTALL_DIR/active-gerrit-workflow/references" \
    "$SKILL_INSTALL_DIR/active-gerrit-workflow/scripts"

  printf '# active-gerrit\n' >"$SKILL_INSTALL_DIR/active-gerrit/SKILL.md"
  printf '# workflow\n' >"$SKILL_INSTALL_DIR/active-gerrit-workflow/SKILL.md"
  printf 'name: active-gerrit\n' >"$SKILL_INSTALL_DIR/active-gerrit/agents/openai.yaml"
  printf 'name: workflow\n' >"$SKILL_INSTALL_DIR/active-gerrit-workflow/agents/openai.yaml"
  printf '# core\n' >"$SKILL_INSTALL_DIR/active-gerrit/references/core-workflows.md"
  printf '# business\n' >"$SKILL_INSTALL_DIR/active-gerrit-workflow/references/business-workflows.md"
  printf '# review\n' >"$SKILL_INSTALL_DIR/active-gerrit-workflow/references/review-policies.md"

  cat >"$SKILL_INSTALL_DIR/install.sh" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "help" || "${1:-}" == "--help" ]]; then
  printf 'Usage:\n'
  exit 0
fi
printf 'stub installer %s\n' "$*"
EOF
  chmod 755 -- "$SKILL_INSTALL_DIR/install.sh"

  mkdir -p -- "$SKILL_INSTALL_DIR/active-gerrit/__pycache__" "$SKILL_INSTALL_DIR/active-gerrit-workflow/.cache"
  printf 'bytecode' >"$SKILL_INSTALL_DIR/active-gerrit/__pycache__/cached.pyc"
  printf 'ignore\n' >"$SKILL_INSTALL_DIR/active-gerrit-workflow/.cache/ignored.txt"

  cat >"$SKILL_INSTALL_DIR/active-gerrit/scripts/gerrit_cli.py" <<'EOF'
import json

print(json.dumps({"ok": True, "command": "doctor", "source": "gerrit", "data": {}, "warnings": []}, sort_keys=True))
EOF
  cat >"$SKILL_INSTALL_DIR/active-gerrit-workflow/scripts/workflow_cli.py" <<'EOF'
import json
import os

print(json.dumps({"ok": True, "command": "doctor", "source": "workflow", "data": {"active_gerrit_home": os.environ.get("ACTIVE_GERRIT_HOME")}, "warnings": []}, sort_keys=True))
EOF
}

prepare_doctor_install() {
  local root="${1:?root is required}"

  DOCTOR_INSTALL_DIR="$root/xdg-data/active-gerrit-workflow"
  DOCTOR_CONFIG_FILE="$root/xdg-config/active-gerrit-workflow/env"

  mkdir -p -- \
    "$DOCTOR_INSTALL_DIR/active-gerrit/scripts" \
    "$DOCTOR_INSTALL_DIR/active-gerrit-workflow/scripts" \
    "$(dirname -- "$DOCTOR_CONFIG_FILE")"

  cat >"$DOCTOR_INSTALL_DIR/active-gerrit/scripts/gerrit_cli.py" <<'EOF'
import json
import os

print(json.dumps({"ok": True, "command": "doctor", "source": "gerrit", "data": {"env_loaded": os.environ.get("TEST_CONFIG_LOADED")}, "warnings": []}, sort_keys=True))
EOF
  cat >"$DOCTOR_INSTALL_DIR/active-gerrit-workflow/scripts/workflow_cli.py" <<'EOF'
import json
import os

print(json.dumps({"ok": True, "command": "doctor", "source": "workflow", "data": {"active_gerrit_home": os.environ.get("ACTIVE_GERRIT_HOME")}, "warnings": []}, sort_keys=True))
EOF

  cat >"$DOCTOR_CONFIG_FILE" <<'EOF'
export TEST_CONFIG_LOADED=1
export GERRIT_BASE_URL=https://gerrit.example.com
export GERRIT_USERNAME=alice
export GERRIT_HTTP_PASSWORD=secret-token
EOF

  git init "$DOCTOR_INSTALL_DIR" >/dev/null 2>&1
  git -C "$DOCTOR_INSTALL_DIR" checkout -B main >/dev/null 2>&1
  git -C "$DOCTOR_INSTALL_DIR" config user.name 'Installer Test' >/dev/null 2>&1
  git -C "$DOCTOR_INSTALL_DIR" config user.email 'installer-test@example.com' >/dev/null 2>&1
  git -C "$DOCTOR_INSTALL_DIR" add . >/dev/null 2>&1
  git -C "$DOCTOR_INSTALL_DIR" commit -m 'init doctor fixture' >/dev/null 2>&1
}

prepare_update_install() {
  local root="${1:?root is required}"
  local config_dir=""
  local config_file=""
  local state_file=""
  local head_commit=""

  UPDATE_REMOTE_REPO="$root/remote.git"
  UPDATE_SEED_REPO="$root/seed"
  UPDATE_INSTALL_DIR="$root/xdg-data/active-gerrit-workflow"
  UPDATE_SKILL_DIR="$root/codex-home/skills"

  git init --bare "$UPDATE_REMOTE_REPO" >/dev/null 2>&1

  mkdir -p -- \
    "$UPDATE_SEED_REPO/active-gerrit/agents" \
    "$UPDATE_SEED_REPO/active-gerrit/references" \
    "$UPDATE_SEED_REPO/active-gerrit/scripts" \
    "$UPDATE_SEED_REPO/active-gerrit-workflow/agents" \
    "$UPDATE_SEED_REPO/active-gerrit-workflow/references" \
    "$UPDATE_SEED_REPO/active-gerrit-workflow/scripts"

  printf '# active-gerrit\n' >"$UPDATE_SEED_REPO/active-gerrit/SKILL.md"
  printf 'name: active-gerrit\n' >"$UPDATE_SEED_REPO/active-gerrit/agents/openai.yaml"
  printf '# core\n' >"$UPDATE_SEED_REPO/active-gerrit/references/core-workflows.md"
  cat >"$UPDATE_SEED_REPO/active-gerrit/scripts/gerrit_cli.py" <<'EOF'
import json

print(json.dumps({"ok": True, "command": "doctor", "source": "gerrit", "data": {}, "warnings": []}, sort_keys=True))
EOF

  printf '# workflow\n' >"$UPDATE_SEED_REPO/active-gerrit-workflow/SKILL.md"
  printf 'name: workflow\n' >"$UPDATE_SEED_REPO/active-gerrit-workflow/agents/openai.yaml"
  printf '# business\n' >"$UPDATE_SEED_REPO/active-gerrit-workflow/references/business-workflows.md"
  printf '# review\n' >"$UPDATE_SEED_REPO/active-gerrit-workflow/references/review-policies.md"
  cat >"$UPDATE_SEED_REPO/active-gerrit-workflow/scripts/workflow_cli.py" <<'EOF'
import json

print(json.dumps({"ok": True, "command": "doctor", "source": "workflow", "data": {}, "warnings": []}, sort_keys=True))
EOF

  git init "$UPDATE_SEED_REPO" >/dev/null 2>&1
  git -C "$UPDATE_SEED_REPO" checkout -B main >/dev/null 2>&1
  git -C "$UPDATE_SEED_REPO" config user.name 'Installer Test' >/dev/null 2>&1
  git -C "$UPDATE_SEED_REPO" config user.email 'installer-test@example.com' >/dev/null 2>&1
  git -C "$UPDATE_SEED_REPO" add . >/dev/null 2>&1
  git -C "$UPDATE_SEED_REPO" commit -m 'init seed' >/dev/null 2>&1
  git -C "$UPDATE_SEED_REPO" remote add origin "$UPDATE_REMOTE_REPO"
  git -C "$UPDATE_SEED_REPO" push -u origin main >/dev/null 2>&1

  git clone --branch main "$UPDATE_REMOTE_REPO" "$UPDATE_INSTALL_DIR" >/dev/null 2>&1

  config_dir="$root/xdg-config/active-gerrit-workflow"
  mkdir -p -- "$config_dir" "$root/xdg-cache/active-gerrit-workflow" "$root/xdg-state/active-gerrit-workflow"
  config_file="$config_dir/env"
  state_file="$config_dir/install-state"

  cat >"$config_file" <<'EOF'
export GERRIT_BASE_URL=https://gerrit.example.com
export GERRIT_USERNAME=alice
export GERRIT_HTTP_PASSWORD=secret-token
EOF

  head_commit="$(git -C "$UPDATE_INSTALL_DIR" rev-parse HEAD)"
  {
    printf 'STATE_INSTALL_DIR=%q\n' "$UPDATE_INSTALL_DIR"
    printf 'STATE_CONFIG_FILE=%q\n' "$config_file"
    printf 'STATE_SKILL_DIR=%q\n' "$UPDATE_SKILL_DIR"
    printf 'STATE_SKILL_MODE=copy\n'
    printf 'STATE_REPO_URL=%q\n' "$UPDATE_REMOTE_REPO"
    printf 'STATE_REF=main\n'
    printf 'STATE_INSTALLED_COMMIT=%s\n' "$head_commit"
    printf 'STATE_INSTALLED_AT=2026-05-12T00:00:00Z\n'
  } >"$state_file"
}