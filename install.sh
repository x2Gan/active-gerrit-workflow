#!/usr/bin/env bash
set -Eeuo pipefail

IFS=$'\n\t'

readonly EXIT_SUCCESS=0
readonly EXIT_FAILURE=1
readonly EXIT_USAGE=2
readonly EXIT_NOT_IMPLEMENTED=3

readonly APP_NAME="active-gerrit-workflow"
readonly DEFAULT_REPO_URL="https://github.com/active-ailab/active-gerrit-workflow.git"
readonly DEFAULT_REF="main"
readonly DEFAULT_SKILL_MODE="symlink"
readonly DEFAULT_ENV_FILENAME="env"
readonly DEFAULT_INSTALL_STATE_FILENAME="install-state"

readonly -a KNOWN_COMMANDS=(
  "install"
  "doctor"
  "config"
  "deploy-skill"
  "update"
  "status"
  "uninstall"
  "help"
)

SCRIPT_NAME="$(basename "$0")"

COMMAND=""
SHOW_HELP=0
VERBOSE=0
NON_INTERACTIVE="${NONINTERACTIVE:-0}"
ASSUME_YES="${YES:-0}"
FORCE="${FORCE:-0}"
INSTALL_DEPS="${ACTIVE_GERRIT_INSTALL_DEPS:-0}"

REPO_URL="${ACTIVE_GERRIT_WORKFLOW_REPO:-}"
REF="${ACTIVE_GERRIT_WORKFLOW_REF:-$DEFAULT_REF}"
INSTALL_DIR="${ACTIVE_GERRIT_WORKFLOW_HOME:-}"
CONFIG_FILE="${ACTIVE_GERRIT_WORKFLOW_ENV_FILE:-}"
CONFIG_DIR=""
SKILL_DIR="${ACTIVE_GERRIT_SKILL_DIR:-}"
SKILL_MODE="${ACTIVE_GERRIT_SKILL_MODE:-$DEFAULT_SKILL_MODE}"
PROFILE_PATH="${PROFILE:-}"
CACHE_DIR="${ACTIVE_GERRIT_WORKFLOW_CACHE_DIR:-}"
STATE_DIR="${ACTIVE_GERRIT_WORKFLOW_STATE_DIR:-}"
BIN_DIR="${ACTIVE_GERRIT_WORKFLOW_BIN_DIR:-}"

DATA_HOME=""
CONFIG_HOME=""
CACHE_HOME=""
STATE_HOME=""
ACTIVE_GERRIT_HOME=""
INSTALL_STATE_FILE=""

RUNTIME_PATHS_INITIALIZED=0

COMMAND_ARGS=()

info() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

error() {
  printf '[ERROR] %s\n' "$*" >&2
}

die() {
  local message="${1:-unknown error}"
  local code="${2:-$EXIT_FAILURE}"
  error "$message"
  exit "$code"
}

usage_error() {
  local message="${1:-invalid usage}"
  error "$message"
  printf 'Run `%s --help` for usage.\n' "$SCRIPT_NAME" >&2
  exit "$EXIT_USAGE"
}

is_known_command() {
  local candidate="${1:-}"
  local known=""
  for known in "${KNOWN_COMMANDS[@]}"; do
    if [[ "$candidate" == "$known" ]]; then
      return 0
    fi
  done
  return 1
}

requires_value() {
  local flag="${1:-}"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    usage_error "Option ${flag} requires a value."
  fi
}

parse_args() {
  local parsing_command=1
  local token=""

  while (($# > 0)); do
    token="$1"
    shift

    case "$token" in
      -h|--help)
        SHOW_HELP=1
        ;;
      --repo-url)
        requires_value "--repo-url" "${1:-}"
        REPO_URL="$1"
        shift
        ;;
      --ref)
        requires_value "--ref" "${1:-}"
        REF="$1"
        shift
        ;;
      --install-dir)
        requires_value "--install-dir" "${1:-}"
        INSTALL_DIR="$1"
        shift
        ;;
      --config-file)
        requires_value "--config-file" "${1:-}"
        CONFIG_FILE="$1"
        shift
        ;;
      --skill-dir)
        requires_value "--skill-dir" "${1:-}"
        SKILL_DIR="$1"
        shift
        ;;
      --skill-mode)
        requires_value "--skill-mode" "${1:-}"
        case "$1" in
          symlink|copy)
            SKILL_MODE="$1"
            ;;
          *)
            usage_error "Unsupported --skill-mode value: $1. Expected `symlink` or `copy`."
            ;;
        esac
        shift
        ;;
      --non-interactive)
        NON_INTERACTIVE=1
        ;;
      --yes)
        ASSUME_YES=1
        ;;
      --force)
        FORCE=1
        ;;
      --verbose)
        VERBOSE=1
        ;;
      --install-deps)
        INSTALL_DEPS=1
        ;;
      --no-profile)
        PROFILE_PATH="/dev/null"
        ;;
      --profile)
        requires_value "--profile" "${1:-}"
        PROFILE_PATH="$1"
        shift
        ;;
      --)
        COMMAND_ARGS+=("$@")
        break
        ;;
      -*)
        usage_error "Unknown option: $token"
        ;;
      *)
        if (( parsing_command )) && is_known_command "$token"; then
          if [[ -n "$COMMAND" ]]; then
            usage_error "Multiple commands were provided: `$COMMAND` and `$token`."
          fi
          COMMAND="$token"
          parsing_command=0
          continue
        fi

        if (( parsing_command )) && [[ -z "$COMMAND" ]]; then
          usage_error "Unknown command: $token"
        fi

        COMMAND_ARGS+=("$token")
        ;;
    esac
  done

  if (( SHOW_HELP )); then
    COMMAND="help"
  elif [[ -z "$COMMAND" ]]; then
    COMMAND="install"
  fi
}

print_help() {
  cat <<EOF
Usage:
  $SCRIPT_NAME [install] [options]
  $SCRIPT_NAME doctor [options]
  $SCRIPT_NAME deploy-skill [options]
  $SCRIPT_NAME config [options]
  $SCRIPT_NAME update [options]
  $SCRIPT_NAME status
  $SCRIPT_NAME uninstall
  $SCRIPT_NAME help

Options:
  --repo-url URL              Source repository URL.
  --ref REF                   Branch, tag, or commit to install. Default: $DEFAULT_REF.
  --install-dir PATH          Source checkout directory.
  --config-file PATH          Runtime env file.
  --skill-dir PATH            Target Codex skills directory.
  --skill-mode MODE           symlink or copy. Default: $DEFAULT_SKILL_MODE.
  --non-interactive           Disable prompts. Same as NONINTERACTIVE=1.
  --yes                       Confirm safe prompts.
  --install-deps              Try to install missing required dependencies.
  --no-profile                Do not modify shell profile.
  --profile PATH              Shell profile to update.
  --force                     Backup and replace installer-managed conflicts.
  --verbose                   Print detailed progress with secrets redacted.
  -h, --help                  Show help.

Environment:
  GERRIT_BASE_URL             Gerrit Web root URL.
  GERRIT_USERNAME             Gerrit username.
  GERRIT_HTTP_PASSWORD        Gerrit UI generated HTTP password.
  ACTIVE_GERRIT_WORKFLOW_REPO Source repository URL override.
  ACTIVE_GERRIT_WORKFLOW_REF  Source branch, tag, or commit override.
  ACTIVE_GERRIT_WORKFLOW_HOME Source checkout directory override.
  ACTIVE_GERRIT_WORKFLOW_CONFIG_DIR
                              Config directory override.
  ACTIVE_GERRIT_WORKFLOW_ENV_FILE
                              Runtime env file override.
  ACTIVE_GERRIT_WORKFLOW_CACHE_DIR
                              Cache directory override.
  ACTIVE_GERRIT_WORKFLOW_STATE_DIR
                              State directory override.
  ACTIVE_GERRIT_WORKFLOW_BIN_DIR
                              Launcher bin directory override.
  ACTIVE_GERRIT_SKILL_DIR     Target Skill directory.
  ACTIVE_GERRIT_SKILL_MODE    symlink or copy.
  ACTIVE_GERRIT_INSTALL_DEPS  Enable dependency installation attempts.
  NONINTERACTIVE=1            Automation mode.
  YES=1                       Assume yes for safe prompts.
  VERBOSE=1                   Enable verbose logging.
EOF
}

xdg_data_home() {
  printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}"
}

xdg_config_home() {
  printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}"
}

xdg_cache_home() {
  printf '%s\n' "${XDG_CACHE_HOME:-$HOME/.cache}"
}

xdg_state_home() {
  printf '%s\n' "${XDG_STATE_HOME:-$HOME/.local/state}"
}

default_skill_dir() {
  if [[ -n "${CODEX_HOME:-}" ]]; then
    printf '%s\n' "$CODEX_HOME/skills"
  else
    printf '%s\n' "$HOME/.codex/skills"
  fi
}

shell_quote() {
  local value="${1-}"
  printf '%q' "$value"
}

command_exists() {
  local command_name="${1:-}"
  command -v "$command_name" >/dev/null 2>&1
}

require_command() {
  local command_name="${1:?command name is required}"
  if ! command_exists "$command_name"; then
    die "Required command \`$command_name\` is not installed or not on PATH."
  fi
}

ensure_dir() {
  local path="${1:?directory path is required}"
  mkdir -p -- "$path"
}

ensure_private_dir() {
  local path="${1:?directory path is required}"
  ensure_dir "$path"
  chmod 700 -- "$path" 2>/dev/null || warn "Could not enforce mode 700 on $path."
}

set_private_file_mode() {
  local path="${1:?file path is required}"
  chmod 600 -- "$path" 2>/dev/null || warn "Could not enforce mode 600 on $path."
}

atomic_write_file() {
  local target="${1:?target path is required}"
  local mode="${2:?mode is required}"
  local content="${3-}"
  local parent_dir=""
  local tmp_file=""

  parent_dir="$(dirname -- "$target")"
  ensure_dir "$parent_dir"
  tmp_file="$(mktemp "$parent_dir/.tmp.XXXXXX")"
  trap 'rm -f -- "$tmp_file"' RETURN

  printf '%s' "$content" >"$tmp_file"
  chmod "$mode" -- "$tmp_file"
  mv -f -- "$tmp_file" "$target"

  trap - RETURN
}

timestamp_now_utc() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

canonicalize_path() {
  local path="${1:?path is required}"
  local parent_dir=""
  local base_name=""

  if [[ -d "$path" ]]; then
    (
      cd -- "$path" >/dev/null 2>&1
      pwd -P
    )
    return 0
  fi

  parent_dir="$(dirname -- "$path")"
  base_name="$(basename -- "$path")"
  (
    cd -- "$parent_dir" >/dev/null 2>&1
    printf '%s/%s\n' "$(pwd -P)" "$base_name"
  )
}

normalize_repo_url() {
  local repo_url="${1:-}"
  local host_path=""
  local host=""
  local path=""
  local file_path=""

  if [[ -z "$repo_url" ]]; then
    printf '\n'
    return 0
  fi

  if [[ -e "$repo_url" ]]; then
    canonicalize_path "$repo_url"
    return 0
  fi

  case "$repo_url" in
    file://*)
      file_path="${repo_url#file://}"
      if [[ -e "$file_path" ]]; then
        printf 'file://%s\n' "$(canonicalize_path "$file_path")"
        return 0
      fi
      ;;
    git@*:*/*)
      host_path="${repo_url#git@}"
      host="${host_path%%:*}"
      path="${host_path#*:}"
      repo_url="ssh://git@${host}/${path#/}"
      ;;
  esac

  repo_url="${repo_url%/}"
  repo_url="${repo_url%.git}"
  printf '%s\n' "$repo_url"
}

repo_urls_match() {
  local expected="${1:-}"
  local actual="${2:-}"

  [[ "$(normalize_repo_url "$expected")" == "$(normalize_repo_url "$actual")" ]]
}

resolve_repo_settings() {
  if [[ -z "$REPO_URL" ]]; then
    REPO_URL="$DEFAULT_REPO_URL"
  fi
}

initialize_runtime_paths() {
  local config_dir_override=""

  if (( RUNTIME_PATHS_INITIALIZED )); then
    return 0
  fi

  DATA_HOME="$(xdg_data_home)"
  CONFIG_HOME="$(xdg_config_home)"
  CACHE_HOME="$(xdg_cache_home)"
  STATE_HOME="$(xdg_state_home)"

  if [[ -z "$INSTALL_DIR" ]]; then
    INSTALL_DIR="$DATA_HOME/$APP_NAME"
  fi

  config_dir_override="${ACTIVE_GERRIT_WORKFLOW_CONFIG_DIR:-}"
  if [[ -z "$CONFIG_FILE" ]]; then
    if [[ -n "$config_dir_override" ]]; then
      CONFIG_DIR="$config_dir_override"
    else
      CONFIG_DIR="$CONFIG_HOME/$APP_NAME"
    fi
    CONFIG_FILE="$CONFIG_DIR/$DEFAULT_ENV_FILENAME"
  else
    CONFIG_DIR="$(dirname -- "$CONFIG_FILE")"
  fi

  if [[ -z "$CACHE_DIR" ]]; then
    CACHE_DIR="$CACHE_HOME/$APP_NAME"
  fi

  if [[ -z "$STATE_DIR" ]]; then
    STATE_DIR="$STATE_HOME/$APP_NAME"
  fi

  if [[ -z "$BIN_DIR" ]]; then
    BIN_DIR="$HOME/.local/bin"
  fi

  if [[ -z "$SKILL_DIR" ]]; then
    SKILL_DIR="$(default_skill_dir)"
  fi

  ACTIVE_GERRIT_HOME="$INSTALL_DIR/active-gerrit"
  INSTALL_STATE_FILE="$CONFIG_DIR/$DEFAULT_INSTALL_STATE_FILENAME"
  RUNTIME_PATHS_INITIALIZED=1
}

render_env_scaffold() {
  local install_dir_q=""
  local active_gerrit_home_q=""
  local gerrit_cache_dir_q=""

  install_dir_q="$(shell_quote "$INSTALL_DIR")"
  active_gerrit_home_q="$(shell_quote "$ACTIVE_GERRIT_HOME")"
  gerrit_cache_dir_q="$(shell_quote "$CACHE_DIR/gerrit")"

  cat <<EOF
# Generated by ${APP_NAME} install.sh.
export ACTIVE_GERRIT_WORKFLOW_HOME=$install_dir_q
export ACTIVE_GERRIT_HOME=$active_gerrit_home_q
export GERRIT_CACHE_DIR=$gerrit_cache_dir_q

# Configure Gerrit connection settings with \`$SCRIPT_NAME config\`.
# export GERRIT_BASE_URL="https://gerrit.example.com"
# export GERRIT_AUTH_TYPE="basic"
# export GERRIT_USERNAME="alice"
# export GERRIT_HTTP_PASSWORD="replace-with-gerrit-http-password"
# export GERRIT_VERIFY_SSL="true"
# export GERRIT_TIMEOUT_SECONDS="30"
# export GERRIT_DEFAULT_NOTIFY="OWNER_REVIEWERS"
EOF
}

STATE_INSTALL_DIR=""
STATE_CONFIG_FILE=""
STATE_SKILL_DIR=""
STATE_SKILL_MODE=""
STATE_REPO_URL=""
STATE_REF=""
STATE_INSTALLED_COMMIT=""
STATE_INSTALLED_AT=""

read_install_state() {
  STATE_INSTALL_DIR=""
  STATE_CONFIG_FILE=""
  STATE_SKILL_DIR=""
  STATE_SKILL_MODE=""
  STATE_REPO_URL=""
  STATE_REF=""
  STATE_INSTALLED_COMMIT=""
  STATE_INSTALLED_AT=""

  if [[ ! -f "$INSTALL_STATE_FILE" ]]; then
    return 0
  fi

  # shellcheck disable=SC1090
  source "$INSTALL_STATE_FILE"
}

render_install_state() {
  local install_dir_q=""
  local config_file_q=""
  local skill_dir_q=""
  local skill_mode_q=""
  local repo_url_q=""
  local ref_q=""
  local installed_commit_q=""
  local installed_at_q=""
  local state_install_dir="${STATE_INSTALL_DIR:-$INSTALL_DIR}"
  local state_config_file="${STATE_CONFIG_FILE:-$CONFIG_FILE}"
  local state_skill_dir="${STATE_SKILL_DIR:-$SKILL_DIR}"
  local state_skill_mode="${STATE_SKILL_MODE:-$SKILL_MODE}"
  local state_repo_url="${STATE_REPO_URL:-$REPO_URL}"
  local state_ref="${STATE_REF:-$REF}"
  local state_installed_commit="${STATE_INSTALLED_COMMIT:-}"
  local state_installed_at="${STATE_INSTALLED_AT:-}"

  install_dir_q="$(shell_quote "$state_install_dir")"
  config_file_q="$(shell_quote "$state_config_file")"
  skill_dir_q="$(shell_quote "$state_skill_dir")"
  skill_mode_q="$(shell_quote "$state_skill_mode")"
  repo_url_q="$(shell_quote "$state_repo_url")"
  ref_q="$(shell_quote "$state_ref")"
  installed_commit_q="$(shell_quote "$state_installed_commit")"
  installed_at_q="$(shell_quote "$state_installed_at")"

  cat <<EOF
# Generated by ${APP_NAME} install.sh.
STATE_INSTALL_DIR=$install_dir_q
STATE_CONFIG_FILE=$config_file_q
STATE_SKILL_DIR=$skill_dir_q
STATE_SKILL_MODE=$skill_mode_q
STATE_REPO_URL=$repo_url_q
STATE_REF=$ref_q
STATE_INSTALLED_COMMIT=$installed_commit_q
STATE_INSTALLED_AT=$installed_at_q
EOF
}

bootstrap_runtime_layout() {
  ensure_private_dir "$CONFIG_DIR"
  ensure_dir "$CACHE_DIR"
  ensure_dir "$STATE_DIR"

  if [[ ! -f "$CONFIG_FILE" ]]; then
    atomic_write_file "$CONFIG_FILE" 600 "$(render_env_scaffold)"
  else
    set_private_file_mode "$CONFIG_FILE"
  fi

  if [[ -f "$INSTALL_STATE_FILE" ]]; then
    read_install_state
  fi

  STATE_INSTALL_DIR="$INSTALL_DIR"
  STATE_CONFIG_FILE="$CONFIG_FILE"
  STATE_SKILL_DIR="$SKILL_DIR"
  STATE_SKILL_MODE="$SKILL_MODE"
  STATE_REPO_URL="$REPO_URL"
  STATE_REF="${STATE_REF:-$REF}"

  atomic_write_file "$INSTALL_STATE_FILE" 600 "$(render_install_state)"
}

should_bootstrap_runtime_layout() {
  case "$COMMAND" in
    install|config|deploy-skill|update)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

log_verbose_context() {
  if (( ! VERBOSE )); then
    return 0
  fi

  info "command=$COMMAND"
  info "repo_url=${REPO_URL:-<unset>}"
  info "ref=${REF:-<unset>}"
  info "data_home=${DATA_HOME:-<unset>}"
  info "config_home=${CONFIG_HOME:-<unset>}"
  info "cache_home=${CACHE_HOME:-<unset>}"
  info "state_home=${STATE_HOME:-<unset>}"
  info "install_dir=${INSTALL_DIR:-<unset>}"
  info "config_dir=${CONFIG_DIR:-<unset>}"
  info "config_file=${CONFIG_FILE:-<unset>}"
  info "cache_dir=${CACHE_DIR:-<unset>}"
  info "state_dir=${STATE_DIR:-<unset>}"
  info "install_state_file=${INSTALL_STATE_FILE:-<unset>}"
  info "skill_dir=${SKILL_DIR:-<unset>}"
  info "skill_mode=${SKILL_MODE:-<unset>}"
  info "non_interactive=$NON_INTERACTIVE"
  info "yes=$ASSUME_YES"
  info "force=$FORCE"
  info "install_deps=$INSTALL_DEPS"
  info "profile=${PROFILE_PATH:-<unset>}"
}

git_repo_root() {
  local repo_dir="${1:?repository path is required}"
  git -C "$repo_dir" rev-parse --show-toplevel 2>/dev/null
}

git_current_commit() {
  local repo_dir="${1:?repository path is required}"
  git -C "$repo_dir" rev-parse HEAD
}

git_current_checkout_ref() {
  local repo_dir="${1:?repository path is required}"
  local branch_name=""

  branch_name="$(git -C "$repo_dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [[ -n "$branch_name" ]]; then
    printf '%s\n' "$branch_name"
    return 0
  fi

  git -C "$repo_dir" rev-parse --short HEAD
}

install_dir_backup_path() {
  local base_path="${1:?path is required}"
  local timestamp=""
  local candidate=""
  local suffix=0

  timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  candidate="${base_path}.bak.${timestamp}"

  while [[ -e "$candidate" ]]; do
    suffix=$((suffix + 1))
    candidate="${base_path}.bak.${timestamp}.${suffix}"
  done

  printf '%s\n' "$candidate"
}

backup_existing_install_dir() {
  local reason="${1:?backup reason is required}"
  local backup_path=""

  backup_path="$(install_dir_backup_path "$INSTALL_DIR")"
  mv -- "$INSTALL_DIR" "$backup_path"
  info "Backed up ${reason} from $INSTALL_DIR to $backup_path"
}

clone_repo_to_dir() {
  local repo_url="${1:?repository URL is required}"
  local ref="${2:?ref is required}"
  local target_dir="${3:?target directory is required}"

  if git clone --origin origin --branch "$ref" -- "$repo_url" "$target_dir"; then
    return 0
  fi

  if [[ "$ref" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
    warn "Ref \`$ref\` looks like a commit SHA; retrying with a detached checkout."
    rm -rf -- "$target_dir"
    git clone --origin origin -- "$repo_url" "$target_dir"
    git -C "$target_dir" fetch --depth 1 origin "$ref"
    git -C "$target_dir" checkout --detach FETCH_HEAD
    return 0
  fi

  return 1
}

clone_source_checkout() {
  local parent_dir=""
  local base_name=""
  local temp_clone_dir=""

  parent_dir="$(dirname -- "$INSTALL_DIR")"
  base_name="$(basename -- "$INSTALL_DIR")"
  ensure_dir "$parent_dir"
  temp_clone_dir="$(mktemp -d "$parent_dir/.${base_name}.clone.XXXXXX")"
  trap 'rm -rf -- "$temp_clone_dir"' RETURN

  info "Cloning $REPO_URL@$REF into $INSTALL_DIR"
  if ! clone_repo_to_dir "$REPO_URL" "$REF" "$temp_clone_dir"; then
    die "Failed to clone \`$REPO_URL\` at ref \`$REF\`."
  fi

  mv -- "$temp_clone_dir" "$INSTALL_DIR"
  trap - RETURN
}

handle_install_dir_conflict() {
  local reason="${1:?conflict reason is required}"

  if (( FORCE )); then
    backup_existing_install_dir "$reason"
    clone_source_checkout
    return 0
  fi

  die "${reason} at $INSTALL_DIR. Re-run with \`--force\` to back it up and install a fresh clone."
}

ensure_existing_repo_matches() {
  local repo_root=""
  local repo_root_real=""
  local install_dir_real=""
  local actual_origin=""
  local current_ref=""

  repo_root="$(git_repo_root "$INSTALL_DIR" || true)"
  if [[ -z "$repo_root" ]]; then
    handle_install_dir_conflict "Install directory exists but is not a Git repository"
    return 0
  fi

  repo_root_real="$(canonicalize_path "$repo_root")"
  install_dir_real="$(canonicalize_path "$INSTALL_DIR")"
  if [[ "$repo_root_real" != "$install_dir_real" ]]; then
    handle_install_dir_conflict "Install directory is nested inside another Git working tree"
    return 0
  fi

  actual_origin="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
  if [[ -z "$actual_origin" ]]; then
    handle_install_dir_conflict "Existing repository has no \`origin\` remote"
    return 0
  fi

  if ! repo_urls_match "$REPO_URL" "$actual_origin"; then
    die "Install directory points at a different repository origin. Expected \`$REPO_URL\`, found \`$actual_origin\`."
  fi

  current_ref="$(git_current_checkout_ref "$INSTALL_DIR")"
  if [[ "$current_ref" != "$REF" ]]; then
    warn "Existing checkout is at \`$current_ref\`; requested ref is \`$REF\`. Leaving the checkout unchanged."
  fi

  info "Install directory already contains the expected repository."
}

refresh_install_state_from_checkout() {
  local checkout_ref=""

  checkout_ref="$REF"
  if [[ -e "$INSTALL_DIR" ]]; then
    checkout_ref="$(git_current_checkout_ref "$INSTALL_DIR")"
  fi

  STATE_INSTALL_DIR="$INSTALL_DIR"
  STATE_CONFIG_FILE="$CONFIG_FILE"
  STATE_SKILL_DIR="$SKILL_DIR"
  STATE_SKILL_MODE="$SKILL_MODE"
  STATE_REPO_URL="$REPO_URL"
  STATE_REF="$checkout_ref"
  STATE_INSTALLED_COMMIT="$(git_current_commit "$INSTALL_DIR")"
  STATE_INSTALLED_AT="$(timestamp_now_utc)"

  atomic_write_file "$INSTALL_STATE_FILE" 600 "$(render_install_state)"
}

sync_source_checkout() {
  require_command git

  if [[ ! -e "$INSTALL_DIR" ]]; then
    clone_source_checkout
    refresh_install_state_from_checkout
    return 0
  fi

  ensure_existing_repo_matches
  refresh_install_state_from_checkout
}

command_stub() {
  local command_name="${1:-unknown}"
  shift || true

  if (($# > 0)); then
    warn "Command \`$command_name\` received reserved arguments: $*"
  fi

  error "Command \`$command_name\` is wired, but its implementation has not landed yet."
  error "This step is reserved for follow-up tasks after M9-T01."
  return "$EXIT_NOT_IMPLEMENTED"
}

handle_install() {
  if (($# > 0)); then
    warn "Command \`install\` ignores reserved arguments: $*"
  fi

  info "Prepared runtime layout:"
  info "  install_dir=$INSTALL_DIR"
  info "  config_file=$CONFIG_FILE"
  info "  install_state_file=$INSTALL_STATE_FILE"
  sync_source_checkout
  info "Source checkout is ready."
  warn "Config generation, Skill deployment, and installer doctor are still pending follow-up tasks."
}

handle_doctor() {
  command_stub "doctor" "$@"
}

handle_config() {
  command_stub "config" "$@"
}

handle_deploy_skill() {
  command_stub "deploy-skill" "$@"
}

handle_update() {
  command_stub "update" "$@"
}

handle_status() {
  if (($# > 0)); then
    warn "Command \`status\` ignores reserved arguments: $*"
  fi

  read_install_state

  printf 'install_dir=%s\n' "$INSTALL_DIR"
  printf 'config_dir=%s\n' "$CONFIG_DIR"
  printf 'config_file=%s\n' "$CONFIG_FILE"
  printf 'config_file_exists=%s\n' "$( [[ -f "$CONFIG_FILE" ]] && printf 'true' || printf 'false' )"
  printf 'install_state_file=%s\n' "$INSTALL_STATE_FILE"
  printf 'install_state_file_exists=%s\n' "$( [[ -f "$INSTALL_STATE_FILE" ]] && printf 'true' || printf 'false' )"
  printf 'cache_dir=%s\n' "$CACHE_DIR"
  printf 'state_dir=%s\n' "$STATE_DIR"
  printf 'skill_dir=%s\n' "$SKILL_DIR"
  printf 'skill_mode=%s\n' "$SKILL_MODE"
  printf 'state_install_dir=%s\n' "${STATE_INSTALL_DIR:-}"
  printf 'state_config_file=%s\n' "${STATE_CONFIG_FILE:-}"
  printf 'state_skill_dir=%s\n' "${STATE_SKILL_DIR:-}"
  printf 'state_skill_mode=%s\n' "${STATE_SKILL_MODE:-}"
  printf 'state_repo_url=%s\n' "${STATE_REPO_URL:-}"
  printf 'state_ref=%s\n' "${STATE_REF:-}"
  printf 'state_installed_commit=%s\n' "${STATE_INSTALLED_COMMIT:-}"
  printf 'state_installed_at=%s\n' "${STATE_INSTALLED_AT:-}"
}

handle_uninstall() {
  command_stub "uninstall" "$@"
}

dispatch_command() {
  case "$COMMAND" in
    help)
      print_help
      ;;
    install)
      handle_install "${COMMAND_ARGS[@]}"
      ;;
    doctor)
      handle_doctor "${COMMAND_ARGS[@]}"
      ;;
    config)
      handle_config "${COMMAND_ARGS[@]}"
      ;;
    deploy-skill)
      handle_deploy_skill "${COMMAND_ARGS[@]}"
      ;;
    update)
      handle_update "${COMMAND_ARGS[@]}"
      ;;
    status)
      handle_status "${COMMAND_ARGS[@]}"
      ;;
    uninstall)
      handle_uninstall "${COMMAND_ARGS[@]}"
      ;;
    *)
      usage_error "Unknown command: $COMMAND"
      ;;
  esac
}

main() {
  parse_args "$@"
  resolve_repo_settings
  initialize_runtime_paths
  if should_bootstrap_runtime_layout; then
    bootstrap_runtime_layout
  fi
  log_verbose_context
  dispatch_command
}

main "$@"
