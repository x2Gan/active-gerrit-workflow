#!/usr/bin/env bash
set -Eeuo pipefail

IFS=$'\n\t'

###############################################################################
# Source distribution
#
# Private repository bootstrap:
#   gh auth login
#   gh auth setup-git
#   mkdir -p active-gerrit-workflow && cd active-gerrit-workflow
#   bash -c "$(gh api --method GET -H 'Accept: application/vnd.github.raw+json' /repos/active-ailab/active-gerrit-workflow/contents/install.sh -f ref=main)"
#
# Private repository bootstrap into an explicit checkout directory:
#   bash -c "$(gh api --method GET -H 'Accept: application/vnd.github.raw+json' /repos/active-ailab/active-gerrit-workflow/contents/install.sh -f ref=main)" -- --install-dir /path/to/active-gerrit-workflow
#
###############################################################################

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
readonly INSTALLER_MANAGED_COPY_MARKER=".active-gerrit-installer-managed"
readonly PROFILE_BLOCK_START="# >>> active-gerrit-workflow >>>"
readonly PROFILE_BLOCK_END="# <<< active-gerrit-workflow <<<"

readonly -a SKILL_NAMES=(
  "active-gerrit"
  "active-gerrit-workflow"
)

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
OUTPUT_JSON=0
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
BIN_DIR_WAS_SET=0
INSTALL_DIR_WAS_SET=0
PROFILE_WAS_SET=0
REPO_URL_WAS_SET=0
FAILURE_NEXT_STEPS_EMITTED=0

if [[ -n "${ACTIVE_GERRIT_WORKFLOW_HOME:-}" ]]; then
  INSTALL_DIR_WAS_SET=1
fi
if [[ -n "${ACTIVE_GERRIT_WORKFLOW_BIN_DIR:-}" ]]; then
  BIN_DIR_WAS_SET=1
fi
if [[ -n "${PROFILE:-}" ]]; then
  PROFILE_WAS_SET=1
fi
if [[ -n "${ACTIVE_GERRIT_WORKFLOW_REPO:-}" ]]; then
  REPO_URL_WAS_SET=1
fi

DATA_HOME=""
CONFIG_HOME=""
CACHE_HOME=""
STATE_HOME=""
ACTIVE_GERRIT_HOME=""
INSTALL_STATE_FILE=""

RUNTIME_PATHS_INITIALIZED=0

COMMAND_ARGS=()

info() {
  printf '[INFO] %s\n' "$(redact_text "$*")"
}

warn() {
  printf '[WARN] %s\n' "$(redact_text "$*")" >&2
}

error() {
  printf '[ERROR] %s\n' "$(redact_text "$*")" >&2
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
  printf "Run \`%s --help\` for usage.\n" "$SCRIPT_NAME" >&2
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
        REPO_URL_WAS_SET=1
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
        INSTALL_DIR_WAS_SET=1
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
            usage_error "Unsupported --skill-mode value: $1. Expected \`symlink\` or \`copy\`."
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
      --json)
        OUTPUT_JSON=1
        ;;
      --install-deps)
        INSTALL_DEPS=1
        ;;
      --no-profile)
        PROFILE_PATH="/dev/null"
        PROFILE_WAS_SET=1
        ;;
      --profile)
        requires_value "--profile" "${1:-}"
        PROFILE_PATH="$1"
        PROFILE_WAS_SET=1
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
            usage_error "Multiple commands were provided: \`$COMMAND\` and \`$token\`."
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

Source distribution for private GitHub repositories:
  gh auth login
  gh auth setup-git
  mkdir -p active-gerrit-workflow && cd active-gerrit-workflow
  bash -c "\$(gh api --method GET -H 'Accept: application/vnd.github.raw+json' /repos/active-ailab/active-gerrit-workflow/contents/install.sh -f ref=main)"
  bash -c "\$(gh api --method GET -H 'Accept: application/vnd.github.raw+json' /repos/active-ailab/active-gerrit-workflow/contents/install.sh -f ref=main)" -- --install-dir /path/to/active-gerrit-workflow

Token fallback without GitHub CLI:
  export GITHUB_TOKEN="github_pat_xxx"
  mkdir -p active-gerrit-workflow && cd active-gerrit-workflow
  curl -fsSL \\
    -H "Authorization: Bearer \${GITHUB_TOKEN:?}" \\
    -H 'Accept: application/vnd.github.raw+json' \\
    'https://api.github.com/repos/active-ailab/active-gerrit-workflow/contents/install.sh?ref=main' | bash

Options:
  --repo-url URL              Source repository URL.
  --ref REF                   Branch, tag, or commit to install. Default: $DEFAULT_REF.
  --install-dir PATH          Source checkout directory. Default: current working directory.
  --config-file PATH          Runtime env file.
  --skill-dir PATH            Target Codex skills directory.
  --skill-mode MODE           symlink or copy. Default: $DEFAULT_SKILL_MODE.
  --non-interactive           Disable prompts. Same as NONINTERACTIVE=1.
  --yes                       Confirm safe prompts.
  --install-deps              Try to install missing required dependencies.
  --no-profile                Do not modify shell profile.
  --profile PATH              Shell profile to update.
  --force                     Backup and replace installer-managed conflicts.
  --json                      Emit machine-readable doctor output.
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

default_install_dir() {
  pwd -P
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

directory_is_empty() {
  local path="${1:?directory path is required}"

  [[ -d "$path" ]] || return 1
  [[ -z "$(find "$path" -mindepth 1 -maxdepth 1 -print -quit)" ]]
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

  if (( REPO_URL_WAS_SET )) && [[ "$REPO_URL" != "$DEFAULT_REPO_URL" ]]; then
    warn "Custom source repository override is active: $REPO_URL"
    warn "Verify the source repository and ref before continuing with the installer."
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
    INSTALL_DIR="$(default_install_dir)"
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

backup_path_for_target() {
  local target="${1:?target path is required}"
  local timestamp=""
  local candidate=""
  local suffix=0

  timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  candidate="${target}.bak.${timestamp}"

  while [[ -e "$candidate" ]]; do
    suffix=$((suffix + 1))
    candidate="${target}.bak.${timestamp}.${suffix}"
  done

  printf '%s\n' "$candidate"
}

backup_existing_path() {
  local target="${1:?target path is required}"
  local backup_path=""

  if [[ ! -e "$target" ]]; then
    return 0
  fi

  backup_path="$(backup_path_for_target "$target")"
  cp -p -- "$target" "$backup_path"
  info "Backed up $(basename -- "$target") to $backup_path"
}

move_existing_path_to_backup() {
  local target="${1:?target path is required}"
  local backup_path=""

  if [[ ! -e "$target" && ! -L "$target" ]]; then
    return 0
  fi

  backup_path="$(backup_path_for_target "$target")"
  mv -- "$target" "$backup_path"
  info "Backed up $(basename -- "$target") to $backup_path"
}

default_gerrit_cache_dir() {
  printf '%s\n' "$CACHE_DIR/gerrit"
}

EXISTING_GERRIT_BASE_URL=""
EXISTING_GERRIT_AUTH_TYPE=""
EXISTING_GERRIT_USERNAME=""
EXISTING_GERRIT_HTTP_PASSWORD=""
EXISTING_GERRIT_VERIFY_SSL=""
EXISTING_GERRIT_TIMEOUT_SECONDS=""
EXISTING_GERRIT_DEFAULT_NOTIFY=""
EXISTING_GERRIT_CACHE_DIR=""

read_existing_runtime_config() {
  EXISTING_GERRIT_BASE_URL=""
  EXISTING_GERRIT_AUTH_TYPE=""
  EXISTING_GERRIT_USERNAME=""
  EXISTING_GERRIT_HTTP_PASSWORD=""
  EXISTING_GERRIT_VERIFY_SSL=""
  EXISTING_GERRIT_TIMEOUT_SECONDS=""
  EXISTING_GERRIT_DEFAULT_NOTIFY=""
  EXISTING_GERRIT_CACHE_DIR=""

  if [[ ! -f "$CONFIG_FILE" ]]; then
    return 0
  fi

  local dumped=""
  dumped="$(
    set +u
    # shellcheck disable=SC1090
    source "$CONFIG_FILE" >/dev/null 2>&1 || exit 0
    printf 'EXISTING_GERRIT_BASE_URL=%q\n' "${GERRIT_BASE_URL:-}"
    printf 'EXISTING_GERRIT_AUTH_TYPE=%q\n' "${GERRIT_AUTH_TYPE:-}"
    printf 'EXISTING_GERRIT_USERNAME=%q\n' "${GERRIT_USERNAME:-}"
    printf 'EXISTING_GERRIT_HTTP_PASSWORD=%q\n' "${GERRIT_HTTP_PASSWORD:-}"
    printf 'EXISTING_GERRIT_VERIFY_SSL=%q\n' "${GERRIT_VERIFY_SSL:-}"
    printf 'EXISTING_GERRIT_TIMEOUT_SECONDS=%q\n' "${GERRIT_TIMEOUT_SECONDS:-}"
    printf 'EXISTING_GERRIT_DEFAULT_NOTIFY=%q\n' "${GERRIT_DEFAULT_NOTIFY:-}"
    printf 'EXISTING_GERRIT_CACHE_DIR=%q\n' "${GERRIT_CACHE_DIR:-}"
  )"

  if [[ -n "$dumped" ]]; then
    eval "$dumped"
  fi
}

config_value_or_default() {
  local preferred="${1-}"
  local fallback="${2-}"
  local default_value="${3-}"

  if [[ -n "$preferred" ]]; then
    printf '%s\n' "$preferred"
    return 0
  fi
  if [[ -n "$fallback" ]]; then
    printf '%s\n' "$fallback"
    return 0
  fi
  printf '%s\n' "$default_value"
}

validate_http_url() {
  local value="${1-}"
  [[ "$value" == http://* || "$value" == https://* ]]
}

validate_boolean_string() {
  local value="${1-}"
  [[ "$value" == "true" || "$value" == "false" ]]
}

validate_positive_integer() {
  local value="${1-}"
  [[ "$value" =~ ^[0-9]+$ ]] && (( value > 0 ))
}

prompt_with_default() {
  local prompt_label="${1:?prompt label is required}"
  local default_value="${2-}"
  local response=""

  if [[ -n "$default_value" ]]; then
    printf '%s [%s]: ' "$prompt_label" "$default_value" >&2
  else
    printf '%s: ' "$prompt_label" >&2
  fi
  IFS= read -r response
  if [[ -n "$response" ]]; then
    printf '%s\n' "$response"
  else
    printf '%s\n' "$default_value"
  fi
}

prompt_yes_no() {
  local prompt_label="${1:?prompt label is required}"
  local default_value="${2:-yes}"
  local response=""
  local normalized=""

  while true; do
    if [[ "$default_value" == "yes" ]]; then
      printf '%s [Y/n]: ' "$prompt_label" >&2
    else
      printf '%s [y/N]: ' "$prompt_label" >&2
    fi

    IFS= read -r response
    normalized="${response,,}"
    if [[ -z "$normalized" ]]; then
      normalized="$default_value"
    fi

    case "$normalized" in
      y|yes)
        printf 'yes\n'
        return 0
        ;;
      n|no)
        printf 'no\n'
        return 0
        ;;
      *)
        warn "Please answer yes or no."
        ;;
    esac
  done
}

prompt_secret() {
  local prompt_label="${1:?prompt label is required}"
  local allow_blank="${2:-0}"
  local has_existing="${3:-0}"
  local response=""

  while true; do
    if [[ "$has_existing" == "1" ]]; then
      printf '%s [press Enter to keep existing]: ' "$prompt_label" >&2
    else
      printf '%s: ' "$prompt_label" >&2
    fi
    IFS= read -r -s response
    printf '\n' >&2

    if [[ -n "$response" ]]; then
      printf '%s\n' "$response"
      return 0
    fi
    if [[ "$has_existing" == "1" ]]; then
      printf '\n'
      return 0
    fi
    if [[ "$allow_blank" == "1" ]]; then
      printf '\n'
      return 0
    fi
    warn "A value is required."
  done
}

print_config_intro() {
  if (( NON_INTERACTIVE )); then
    return 0
  fi

  printf '\nGerrit runtime configuration\n' >&2
  printf '  Config file: %s\n' "$CONFIG_FILE" >&2
  printf '  Please enter the Gerrit connection settings. You can prefill answers with environment variables.\n' >&2
  printf '  Common variables: GERRIT_BASE_URL, GERRIT_USERNAME, GERRIT_HTTP_PASSWORD, GERRIT_VERIFY_SSL, GERRIT_TIMEOUT_SECONDS.\n' >&2
  if [[ -f "$CONFIG_FILE" ]]; then
    printf '  Existing values are shown as defaults; press Enter to keep them.\n' >&2
  fi
  printf '  Secret values are read without echo and are redacted from installer output.\n\n' >&2
}

render_runtime_env_file() {
  local base_url="${1:?base url is required}"
  local auth_type="${2:?auth type is required}"
  local username="${3:?username is required}"
  local password="${4-}"
  local save_password="${5:?save password flag is required}"
  local verify_ssl="${6:?verify_ssl is required}"
  local timeout_seconds="${7:?timeout is required}"
  local default_notify="${8:?default notify is required}"
  local gerrit_cache_dir="${9:?cache dir is required}"
  local install_dir_q=""
  local active_gerrit_home_q=""
  local gerrit_cache_dir_q=""
  local base_url_q=""
  local auth_type_q=""
  local username_q=""
  local password_q=""
  local verify_ssl_q=""
  local timeout_q=""
  local notify_q=""

  install_dir_q="$(shell_quote "$INSTALL_DIR")"
  active_gerrit_home_q="$(shell_quote "$ACTIVE_GERRIT_HOME")"
  gerrit_cache_dir_q="$(shell_quote "$gerrit_cache_dir")"
  base_url_q="$(shell_quote "$base_url")"
  auth_type_q="$(shell_quote "$auth_type")"
  username_q="$(shell_quote "$username")"
  verify_ssl_q="$(shell_quote "$verify_ssl")"
  timeout_q="$(shell_quote "$timeout_seconds")"
  notify_q="$(shell_quote "$default_notify")"
  password_q="$(shell_quote "$password")"

  cat <<EOF
# Generated by ${APP_NAME} install.sh.
export ACTIVE_GERRIT_WORKFLOW_HOME=$install_dir_q
export ACTIVE_GERRIT_HOME=$active_gerrit_home_q
export GERRIT_CACHE_DIR=$gerrit_cache_dir_q

export GERRIT_BASE_URL=$base_url_q
export GERRIT_AUTH_TYPE=$auth_type_q
export GERRIT_USERNAME=$username_q
EOF

  if [[ "$save_password" == "1" && -n "$password" ]]; then
    printf 'export GERRIT_HTTP_PASSWORD=%s\n' "$password_q"
  else
    cat <<'EOF'
# GERRIT_HTTP_PASSWORD is intentionally omitted.
# Export it in your shell before running Gerrit commands when needed.
EOF
  fi

  cat <<EOF
export GERRIT_VERIFY_SSL=$verify_ssl_q
export GERRIT_TIMEOUT_SECONDS=$timeout_q
export GERRIT_DEFAULT_NOTIFY=$notify_q
EOF
}

skill_source_dir() {
  local skill_name="${1:?skill name is required}"
  printf '%s\n' "$INSTALL_DIR/$skill_name"
}

skill_target_dir() {
  local skill_name="${1:?skill name is required}"
  printf '%s\n' "$SKILL_DIR/$skill_name"
}

skill_marker_file() {
  local target_dir="${1:?target dir is required}"
  printf '%s\n' "$target_dir/$INSTALLER_MANAGED_COPY_MARKER"
}

write_skill_copy_marker() {
  local target_dir="${1:?target dir is required}"
  local skill_name="${2:?skill name is required}"
  local source_dir="${3:?source dir is required}"
  local marker_file=""
  local marker_content=""

  marker_file="$(skill_marker_file "$target_dir")"
  marker_content=$(
    cat <<EOF
# Generated by ${APP_NAME} install.sh.
SKILL_NAME=$(shell_quote "$skill_name")
SOURCE_DIR=$(shell_quote "$source_dir")
SKILL_MODE=$(shell_quote "$SKILL_MODE")
UPDATED_AT=$(shell_quote "$(timestamp_now_utc)")
EOF
  )
  atomic_write_file "$marker_file" 600 "$marker_content"
}

is_installer_managed_copy_dir() {
  local target_dir="${1:?target dir is required}"
  [[ -f "$(skill_marker_file "$target_dir")" ]]
}

validate_skill_source() {
  local skill_name="${1:?skill name is required}"
  local source_dir=""

  source_dir="$(skill_source_dir "$skill_name")"
  if [[ ! -d "$source_dir" ]]; then
    die "Missing source Skill directory: $source_dir"
  fi
  if [[ ! -f "$source_dir/SKILL.md" ]]; then
    die "Missing Skill manifest: $source_dir/SKILL.md"
  fi
}

is_correct_skill_symlink() {
  local target_dir="${1:?target dir is required}"
  local source_dir="${2:?source dir is required}"
  local target_real=""
  local source_real=""

  if [[ ! -L "$target_dir" || ! -d "$target_dir" ]]; then
    return 1
  fi

  target_real="$(canonicalize_path "$target_dir")"
  source_real="$(canonicalize_path "$source_dir")"
  [[ "$target_real" == "$source_real" ]]
}

skill_copy_with_rsync() {
  local source_dir="${1:?source dir is required}"
  local target_dir="${2:?target dir is required}"

  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.cache/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    -- "$source_dir/" "$target_dir/"
}

prune_skill_copy_artifacts() {
  local target_dir="${1:?target dir is required}"

  find "$target_dir" -type d \( -name .git -o -name .cache -o -name __pycache__ \) -prune -exec rm -rf {} +
  find "$target_dir" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete
}

skill_copy_with_cp() {
  local source_dir="${1:?source dir is required}"
  local target_dir="${2:?target dir is required}"
  local parent_dir=""
  local base_name=""
  local temp_dir=""

  parent_dir="$(dirname -- "$target_dir")"
  base_name="$(basename -- "$target_dir")"
  ensure_dir "$parent_dir"
  temp_dir="$(mktemp -d "$parent_dir/.${base_name}.copy.XXXXXX")"
  cp -R -- "$source_dir/." "$temp_dir/"
  prune_skill_copy_artifacts "$temp_dir"

  rm -rf -- "$target_dir"
  mv -- "$temp_dir" "$target_dir"
}

sync_skill_copy_dir() {
  local source_dir="${1:?source dir is required}"
  local target_dir="${2:?target dir is required}"

  if command_exists rsync; then
    ensure_dir "$target_dir"
    skill_copy_with_rsync "$source_dir" "$target_dir"
    prune_skill_copy_artifacts "$target_dir"
  else
    skill_copy_with_cp "$source_dir" "$target_dir"
  fi
}

deploy_skill_symlink() {
  local skill_name="${1:?skill name is required}"
  local source_dir=""
  local target_dir=""

  source_dir="$(skill_source_dir "$skill_name")"
  target_dir="$(skill_target_dir "$skill_name")"
  validate_skill_source "$skill_name"

  if is_correct_skill_symlink "$target_dir" "$source_dir"; then
    info "Skill \`$skill_name\` is already linked correctly."
    return 0
  fi

  if [[ -L "$target_dir" || -e "$target_dir" ]]; then
    if (( ! FORCE )); then
      die "Skill target already exists at $target_dir. Re-run with \`--force\` to back it up and replace it."
    fi
    move_existing_path_to_backup "$target_dir"
  fi

  ensure_dir "$SKILL_DIR"
  ln -s -- "$source_dir" "$target_dir"
  info "Linked skill \`$skill_name\` -> $source_dir"
}

deploy_skill_copy() {
  local skill_name="${1:?skill name is required}"
  local source_dir=""
  local target_dir=""

  source_dir="$(skill_source_dir "$skill_name")"
  target_dir="$(skill_target_dir "$skill_name")"
  validate_skill_source "$skill_name"

  if [[ -L "$target_dir" ]]; then
    if (( ! FORCE )); then
      die "Skill target at $target_dir is a symlink. Re-run with \`--force\` to replace it with a managed copy."
    fi
    move_existing_path_to_backup "$target_dir"
  elif [[ -e "$target_dir" && ! -d "$target_dir" ]]; then
    if (( ! FORCE )); then
      die "Skill target at $target_dir is not a directory. Re-run with \`--force\` to replace it."
    fi
    move_existing_path_to_backup "$target_dir"
  elif [[ -d "$target_dir" ]]; then
    if ! is_installer_managed_copy_dir "$target_dir"; then
      if (( ! FORCE )); then
        die "Skill target at $target_dir already exists and is not installer-managed. Re-run with \`--force\` to back it up and replace it."
      fi
      move_existing_path_to_backup "$target_dir"
    fi
  fi

  ensure_dir "$SKILL_DIR"
  sync_skill_copy_dir "$source_dir" "$target_dir"
  write_skill_copy_marker "$target_dir" "$skill_name" "$source_dir"
  info "Copied skill \`$skill_name\` into $target_dir"
}

path_contains_dir() {
  local target_dir="${1-}"
  local entries=()
  local entry=""

  if [[ -z "$target_dir" ]]; then
    return 1
  fi

  IFS=':' read -r -a entries <<<"${PATH:-}"
  for entry in "${entries[@]}"; do
    if [[ "$entry" == "$target_dir" ]]; then
      return 0
    fi
  done

  return 1
}

launcher_path() {
  local launcher_name="${1:?launcher name is required}"
  printf '%s\n' "$BIN_DIR/$launcher_name"
}

render_launcher_script() {
  local launcher_name="${1:?launcher name is required}"
  local config_file_q=""
  local install_dir_q=""
  local active_gerrit_home_q=""
  local launcher_exec=""

  config_file_q="$(shell_quote "$CONFIG_FILE")"
  install_dir_q="$(shell_quote "$INSTALL_DIR")"
  active_gerrit_home_q="$(shell_quote "$ACTIVE_GERRIT_HOME")"

  case "$launcher_name" in
    active-gerrit)
      # shellcheck disable=SC2016
      launcher_exec='exec python3 "$ACTIVE_GERRIT_HOME/scripts/gerrit_cli.py" "$@"'
      ;;
    active-gerrit-workflow)
      # shellcheck disable=SC2016
      launcher_exec='exec python3 "$ACTIVE_GERRIT_WORKFLOW_HOME/active-gerrit-workflow/scripts/workflow_cli.py" "$@"'
      ;;
    active-gerrit-install)
      # shellcheck disable=SC2016
      launcher_exec='exec bash "$ACTIVE_GERRIT_WORKFLOW_HOME/install.sh" "$@"'
      ;;
    *)
      die "Unsupported launcher name: $launcher_name"
      ;;
  esac

  cat <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="\${ACTIVE_GERRIT_WORKFLOW_ENV_FILE:-$config_file_q}"
export ACTIVE_GERRIT_WORKFLOW_HOME="\${ACTIVE_GERRIT_WORKFLOW_HOME:-$install_dir_q}"
export ACTIVE_GERRIT_HOME="\${ACTIVE_GERRIT_HOME:-$active_gerrit_home_q}"
export ACTIVE_GERRIT_WORKFLOW_ENV_FILE="\$CONFIG_FILE"

if [[ -r "\$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "\$CONFIG_FILE"
fi

$launcher_exec
EOF
}

write_launcher_script() {
  local launcher_name="${1:?launcher name is required}"
  local target_path=""
  local content=""

  target_path="$(launcher_path "$launcher_name")"
  content="$(render_launcher_script "$launcher_name")"
  atomic_write_file "$target_path" 755 "$content"
  info "Generated launcher \`$launcher_name\` at $target_path"
}

ensure_runtime_launchers() {
  ensure_dir "$BIN_DIR"

  write_launcher_script "active-gerrit"
  write_launcher_script "active-gerrit-workflow"
  write_launcher_script "active-gerrit-install"

  if path_contains_dir "$BIN_DIR"; then
    info "Launcher directory is present on PATH: $BIN_DIR"
  else
    warn "Launcher directory is not present on PATH: $BIN_DIR"
    warn "Add $BIN_DIR to PATH to run the generated launchers without a full path."
  fi
}

render_profile_block() {
  local config_file_q=""

  config_file_q="$(shell_quote "$CONFIG_FILE")"

  cat <<EOF
$PROFILE_BLOCK_START
export ACTIVE_GERRIT_WORKFLOW_ENV_FILE=$config_file_q
[ -r "\$ACTIVE_GERRIT_WORKFLOW_ENV_FILE" ] && . "\$ACTIVE_GERRIT_WORKFLOW_ENV_FILE"
$PROFILE_BLOCK_END
EOF
}

file_mode_or_default() {
  local path="${1:?path is required}"
  local default_mode="${2:-600}"
  local mode=""

  if [[ -e "$path" ]]; then
    mode="$(stat -c '%a' "$path" 2>/dev/null || stat -f '%Lp' "$path" 2>/dev/null || true)"
  fi

  if [[ "$mode" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$mode"
  else
    printf '%s\n' "$default_mode"
  fi
}

strip_managed_profile_block() {
  local profile_file="${1:?profile path is required}"
  local line=""
  local inside_block=0
  local content=""

  if [[ ! -f "$profile_file" ]]; then
    printf '\n'
    return 0
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$PROFILE_BLOCK_START" ]]; then
      inside_block=1
      continue
    fi

    if (( inside_block )); then
      if [[ "$line" == "$PROFILE_BLOCK_END" ]]; then
        inside_block=0
      fi
      continue
    fi

    content+="$line"$'\n'
  done < "$profile_file"

  printf '%s' "$content"
}

write_managed_profile_block() {
  local profile_file="${1:?profile path is required}"
  local existing_content=""
  local content=""
  local profile_block=""
  local mode=""

  ensure_dir "$(dirname -- "$profile_file")"
  existing_content="$(strip_managed_profile_block "$profile_file")"
  profile_block="$(render_profile_block)"
  mode="$(file_mode_or_default "$profile_file" 600)"

  content="${existing_content%$'\n'}"
  if [[ -n "$content" ]]; then
    content+=$'\n\n'
  fi
  content+="$profile_block"$'\n'

  atomic_write_file "$profile_file" "$mode" "$content"
  info "Updated managed shell profile block in $profile_file"
}

maybe_update_profile() {
  if [[ "$PROFILE_PATH" == "/dev/null" ]]; then
    info "Skipping shell profile integration because PROFILE=/dev/null."
    return 0
  fi

  if [[ -z "$PROFILE_PATH" ]]; then
    info "Skipping shell profile integration. Re-run with --profile PATH to add a managed source block."
    return 0
  fi

  write_managed_profile_block "$PROFILE_PATH"
}

STATE_INSTALL_DIR=""
STATE_CONFIG_FILE=""
STATE_SKILL_DIR=""
STATE_SKILL_MODE=""
STATE_BIN_DIR=""
STATE_PROFILE_PATH=""
STATE_REPO_URL=""
STATE_REF=""
STATE_INSTALLED_COMMIT=""
STATE_INSTALLED_AT=""

read_install_state() {
  STATE_INSTALL_DIR=""
  STATE_CONFIG_FILE=""
  STATE_SKILL_DIR=""
  STATE_SKILL_MODE=""
  STATE_BIN_DIR=""
  STATE_PROFILE_PATH=""
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
  local bin_dir_q=""
  local profile_path_q=""
  local repo_url_q=""
  local ref_q=""
  local installed_commit_q=""
  local installed_at_q=""
  local state_install_dir="${STATE_INSTALL_DIR:-$INSTALL_DIR}"
  local state_config_file="${STATE_CONFIG_FILE:-$CONFIG_FILE}"
  local state_skill_dir="${STATE_SKILL_DIR:-$SKILL_DIR}"
  local state_skill_mode="${STATE_SKILL_MODE:-$SKILL_MODE}"
  local state_bin_dir="${STATE_BIN_DIR:-$BIN_DIR}"
  local state_profile_path="${STATE_PROFILE_PATH:-$PROFILE_PATH}"
  local state_repo_url="${STATE_REPO_URL:-$REPO_URL}"
  local state_ref="${STATE_REF:-$REF}"
  local state_installed_commit="${STATE_INSTALLED_COMMIT:-}"
  local state_installed_at="${STATE_INSTALLED_AT:-}"

  install_dir_q="$(shell_quote "$state_install_dir")"
  config_file_q="$(shell_quote "$state_config_file")"
  skill_dir_q="$(shell_quote "$state_skill_dir")"
  skill_mode_q="$(shell_quote "$state_skill_mode")"
  bin_dir_q="$(shell_quote "$state_bin_dir")"
  profile_path_q="$(shell_quote "$state_profile_path")"
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
STATE_BIN_DIR=$bin_dir_q
STATE_PROFILE_PATH=$profile_path_q
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

  if [[ "$COMMAND" != "install" ]] && (( ! INSTALL_DIR_WAS_SET )) && [[ -n "${STATE_INSTALL_DIR:-}" ]]; then
    INSTALL_DIR="$STATE_INSTALL_DIR"
    ACTIVE_GERRIT_HOME="$INSTALL_DIR/active-gerrit"
  fi

  STATE_INSTALL_DIR="$INSTALL_DIR"
  STATE_CONFIG_FILE="$CONFIG_FILE"
  STATE_SKILL_DIR="$SKILL_DIR"
  STATE_SKILL_MODE="$SKILL_MODE"
  STATE_BIN_DIR="$BIN_DIR"
  STATE_PROFILE_PATH="$PROFILE_PATH"
  if [[ -z "${STATE_REPO_URL:-}" || "$REPO_URL" != "$DEFAULT_REPO_URL" ]]; then
    STATE_REPO_URL="$REPO_URL"
  fi
  if [[ -z "${STATE_REF:-}" || "$REF" != "$DEFAULT_REF" ]]; then
    STATE_REF="$REF"
  fi

  atomic_write_file "$INSTALL_STATE_FILE" 600 "$(render_install_state)"
}

should_bootstrap_runtime_layout() {
  case "$COMMAND" in
    install|config|deploy-skill)
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
  info "bin_dir=${BIN_DIR:-<unset>}"
  info "non_interactive=$NON_INTERACTIVE"
  info "yes=$ASSUME_YES"
  info "force=$FORCE"
  info "install_deps=$INSTALL_DEPS"
  info "profile=${PROFILE_PATH:-<unset>}"
  info "json=$OUTPUT_JSON"
}

secret_values() {
  local key=""
  for key in \
    GERRIT_HTTP_PASSWORD \
    GERRIT_BEARER_TOKEN \
    GERRIT_ACCESS_TOKEN \
    GERRIT_COOKIE \
    GERRIT_XSRF_TOKEN
  do
    if [[ -n "${!key:-}" ]]; then
      printf '%s\n' "${!key}"
    fi
  done

  if [[ -n "${CONFIG_FILE:-}" && -f "$CONFIG_FILE" ]]; then
    (
      set +u
      # shellcheck disable=SC1090
      source "$CONFIG_FILE" >/dev/null 2>&1 || exit 0
      for key in \
        GERRIT_HTTP_PASSWORD \
        GERRIT_BEARER_TOKEN \
        GERRIT_ACCESS_TOKEN \
        GERRIT_COOKIE \
        GERRIT_XSRF_TOKEN
      do
        if [[ -n "${!key:-}" ]]; then
          printf '%s\n' "${!key}"
        fi
      done
    )
  fi
}

redact_text() {
  local text="${1-}"
  local secret=""

  while [[ "$text" =~ (https?://[^/@[:space:]]+:)([^@[:space:]]+)(@) ]]; do
    if [[ "${BASH_REMATCH[2]}" == "<redacted>" ]]; then
      break
    fi
    text="${text/${BASH_REMATCH[0]}/${BASH_REMATCH[1]}<redacted>${BASH_REMATCH[3]}}"
  done

  while [[ "$text" =~ ([Aa]uthorization[[:space:]"']*[:=][[:space:]"']*)([^[:space:]"']+) ]]; do
    if [[ "${BASH_REMATCH[2]}" == "<redacted>" ]]; then
      break
    fi
    text="${text/${BASH_REMATCH[0]}/${BASH_REMATCH[1]}<redacted>}"
  done

  while [[ "$text" =~ ((password|passwd|token|cookie)[[:space:]"']*[:=][[:space:]"']*)([^[:space:]"']+) ]]; do
    if [[ "${BASH_REMATCH[3]}" == "<redacted>" ]]; then
      break
    fi
    text="${text/${BASH_REMATCH[0]}/${BASH_REMATCH[1]}<redacted>}"
  done

  while IFS= read -r secret; do
    if [[ -n "$secret" ]]; then
      text="${text//"$secret"/<redacted>}"
    fi
  done < <(secret_values)

  printf '%s' "$text"
}

json_escape() {
  local text="${1-}"
  text="$(redact_text "$text")"
  text="${text//\\/\\\\}"
  text="${text//\"/\\\"}"
  text="${text//$'\n'/\\n}"
  text="${text//$'\r'/\\r}"
  text="${text//$'\t'/\\t}"
  printf '%s' "$text"
}

json_quote() {
  printf '"%s"' "$(json_escape "${1-}")"
}

json_bool() {
  if [[ "${1:-0}" == "1" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

json_join_array() {
  local -n values_ref="$1"
  local separator="${2:-,}"
  local result=""
  local value=""

  for value in "${values_ref[@]}"; do
    if [[ -n "$result" ]]; then
      result+="$separator"
    fi
    result+="$value"
  done

  printf '%s' "$result"
}

json_object_from_entries() {
  # shellcheck disable=SC2034
  local -n entries_ref="$1"
  printf '{%s}' "$(json_join_array entries_ref)"
}

json_array_from_entries() {
  # shellcheck disable=SC2034
  local -n entries_ref="$1"
  printf '[%s]' "$(json_join_array entries_ref)"
}

json_string_field() {
  local json="${1-}"
  local field="${2:?field is required}"
  local marker="\"${field}\":\""
  local rest=""
  local value=""
  local char=""
  local escaped=0
  local i=0

  if [[ "$json" != *"$marker"* ]]; then
    return 1
  fi

  rest="${json#*"$marker"}"
  while (( i < ${#rest} )); do
    char="${rest:i:1}"
    if (( escaped )); then
      case "$char" in
        n) value+=$'\n' ;;
        r) value+=$'\r' ;;
        t) value+=$'\t' ;;
        \\) value+=$'\\' ;;
        \") value+='"' ;;
        *) value+="$char" ;;
      esac
      escaped=0
      i=$((i + 1))
      continue
    fi

    case "$char" in
      \\)
        escaped=1
        ;;
      \")
        printf '%s' "$value"
        return 0
        ;;
      *)
        value+="$char"
        ;;
    esac
    i=$((i + 1))
  done

  return 1
}

append_object_entry() {
  local array_name="${1:?array name is required}"
  local key="${2:?key is required}"
  local value_json="${3:?json value is required}"
  local -n array_ref="$array_name"
  array_ref+=("$(json_quote "$key"):$value_json")
}

append_json_string() {
  local array_name="${1:?array name is required}"
  local value="${2-}"
  # shellcheck disable=SC2178
  local -n array_ref="$array_name"
  array_ref+=("$(json_quote "$value")")
}

doctor_check_json() {
  local required="${1:?required flag is required}"
  local ok="${2:?ok flag is required}"
  local summary="${3-}"
  local hint="${4-}"
  local extra_entries="${5-}"
  local entries=()

  entries+=("\"required\":$(json_bool "$required")")
  entries+=("\"ok\":$(json_bool "$ok")")
  if [[ -n "$summary" ]]; then
    entries+=("\"summary\":$(json_quote "$summary")")
  fi
  if [[ -n "$hint" ]]; then
    entries+=("\"hint\":$(json_quote "$hint")")
  fi
  if [[ -n "$extra_entries" ]]; then
    entries+=("$extra_entries")
  fi

  printf '{%s}' "$(json_join_array entries)"
}

command_version_line() {
  local command_name="${1:?command name is required}"
  shift || true
  local output=""

  output="$("$command_name" "$@" 2>&1 || true)"
  IFS=$'\n' read -r output _ <<<"$output"
  printf '%s' "$output"
}

detect_package_manager() {
  if command_exists brew; then
    printf 'brew'
    return 0
  fi
  if [[ -r /etc/os-release ]]; then
    if grep -Eq '^ID(_LIKE)?=.*(debian|ubuntu)' /etc/os-release 2>/dev/null; then
      printf 'apt'
      return 0
    fi
    if grep -Eq '^ID(_LIKE)?=.*(rhel|fedora|centos)' /etc/os-release 2>/dev/null; then
      if command_exists dnf; then
        printf 'dnf'
      else
        printf 'yum'
      fi
      return 0
    fi
  fi
  printf 'generic'
}

install_hint_for_packages() {
  local packages_csv="${1:?packages list is required}"
  local manager=""

  manager="$(detect_package_manager)"
  case "$manager" in
    brew)
      printf 'Install with Homebrew: brew install %s' "$packages_csv"
      ;;
    apt)
      printf 'Install with apt: sudo apt-get update && sudo apt-get install -y %s' "$packages_csv"
      ;;
    dnf)
      printf 'Install with dnf: sudo dnf install -y %s' "$packages_csv"
      ;;
    yum)
      printf 'Install with yum: sudo yum install -y %s' "$packages_csv"
      ;;
    *)
      printf 'Install %s with your system package manager and re-run doctor.' "$packages_csv"
      ;;
  esac
}

install_hint_for_command() {
  local command_name="${1:?command name is required}"
  case "$command_name" in
    bash)
      printf 'Run the installer with Bash, for example: bash install.sh doctor'
      ;;
    python3)
      install_hint_for_packages "python3"
      ;;
    curl_or_wget)
      install_hint_for_packages "curl wget"
      ;;
    jq|openssl|ssh|rg|shellcheck|bats|git|curl|wget|sed)
      install_hint_for_packages "$command_name"
      ;;
    *)
      printf 'Install %s and re-run doctor.' "$command_name"
      ;;
  esac
}

command_check() {
  local command_name="${1:?command name is required}"
  local required="${2:?required flag is required}"
  local version_args_csv="${3-}"
  local hint=""
  local command_path=""
  local version_line=""
  local extra=""

  hint="$(install_hint_for_command "$command_name")"
  if ! command_exists "$command_name"; then
    printf '%s' "$(doctor_check_json "$required" 0 "Command \`$command_name\` is not available." "$hint")"
    return 0
  fi

  command_path="$(command -v "$command_name")"
  if [[ -n "$version_args_csv" ]]; then
    local -a version_args=()
    IFS=' ' read -r -a version_args <<<"$version_args_csv"
    version_line="$(command_version_line "$command_name" "${version_args[@]}")"
  fi

  extra="\"path\":$(json_quote "$command_path")"
  if [[ -n "$version_line" ]]; then
    extra+=",\"version\":$(json_quote "$version_line")"
  fi
  printf '%s' "$(doctor_check_json "$required" 1 "Command \`$command_name\` is available." "" "$extra")"
}

curl_or_wget_check() {
  local curl_available=0
  local wget_available=0
  local hint=""
  local providers=()
  local extra=""

  if command_exists curl; then
    curl_available=1
    providers+=("$(json_quote "curl")")
  fi
  if command_exists wget; then
    wget_available=1
    providers+=("$(json_quote "wget")")
  fi

  hint="$(install_hint_for_command "curl_or_wget")"
  if (( ! curl_available && ! wget_available )); then
    printf '%s' "$(doctor_check_json 1 0 "Neither \`curl\` nor \`wget\` is available." "$hint")"
    return 0
  fi

  extra="\"providers\":$(json_array_from_entries providers)"
  printf '%s' "$(doctor_check_json 1 1 "At least one download command is available." "" "$extra")"
}

python_version_check() {
  local hint=""
  local version_output=""
  local extra=""

  hint="$(install_hint_for_command "python3")"
  if ! command_exists python3; then
    printf '%s' "$(doctor_check_json 1 0 "Command \`python3\` is not available." "$hint")"
    return 0
  fi

  version_output="$(python3 -c 'import platform, sys; print(platform.python_version()); raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>&1 || true)"
  version_output="$(redact_text "$version_output")"
  if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    extra="\"path\":$(json_quote "$(command -v python3)")"
    if [[ -n "$version_output" ]]; then
      extra+=",\"version\":$(json_quote "$version_output")"
    fi
    printf '%s' "$(doctor_check_json 1 1 "Python 3.9+ is available." "" "$extra")"
    return 0
  fi

  extra="\"path\":$(json_quote "$(command -v python3)")"
  if [[ -n "$version_output" ]]; then
    extra+=",\"version\":$(json_quote "$version_output")"
  fi
  printf '%s' "$(doctor_check_json 1 0 "Python 3.9+ is required." "$hint" "$extra")"
}

directory_access_check() {
  local path="${1:?path is required}"
  local required="${2:?required flag is required}"
  local label="${3:?label is required}"
  local must_exist="${4:-1}"
  local summary=""
  local hint=""
  local extra=""

  if [[ ! -e "$path" ]]; then
    if [[ "$must_exist" == "1" ]]; then
      hint="Create $label by running \`$SCRIPT_NAME install\` or point --install-dir/--config-file at an existing installation."
      printf '%s' "$(doctor_check_json "$required" 0 "$label does not exist." "$hint" "\"path\":$(json_quote "$path")")"
      return 0
    fi

    if [[ -w "$(dirname -- "$path")" ]]; then
      printf '%s' "$(doctor_check_json "$required" 1 "$label can be created." "" "\"path\":$(json_quote "$path")")"
    else
      hint="Ensure the parent directory of $label is writable."
      printf '%s' "$(doctor_check_json "$required" 0 "$label does not exist and cannot be created." "$hint" "\"path\":$(json_quote "$path")")"
    fi
    return 0
  fi

  if [[ ! -d "$path" ]]; then
    hint="Move or remove the non-directory path at $path."
    printf '%s' "$(doctor_check_json "$required" 0 "$label is not a directory." "$hint" "\"path\":$(json_quote "$path")")"
    return 0
  fi

  if [[ ! -r "$path" || ! -w "$path" ]]; then
    hint="Adjust permissions so $label is readable and writable."
    printf '%s' "$(doctor_check_json "$required" 0 "$label is not readable and writable." "$hint" "\"path\":$(json_quote "$path")")"
    return 0
  fi

  extra="\"path\":$(json_quote "$path")"
  printf '%s' "$(doctor_check_json "$required" 1 "$label is readable and writable." "" "$extra")"
}

config_file_check() {
  local extra=""
  local hint=""

  if [[ ! -f "$CONFIG_FILE" ]]; then
    hint="Run \`$SCRIPT_NAME config\` after install to create the runtime env file."
    printf '%s' "$(doctor_check_json 0 0 "Runtime env file is missing." "$hint" "\"path\":$(json_quote "$CONFIG_FILE")")"
    return 0
  fi

  if [[ ! -r "$CONFIG_FILE" ]]; then
    hint="Adjust permissions so the runtime env file is readable."
    printf '%s' "$(doctor_check_json 1 0 "Runtime env file is not readable." "$hint" "\"path\":$(json_quote "$CONFIG_FILE")")"
    return 0
  fi

  extra="\"path\":$(json_quote "$CONFIG_FILE"),\"mode\":$(json_quote "$(stat -c '%a' "$CONFIG_FILE" 2>/dev/null || stat -f '%Lp' "$CONFIG_FILE" 2>/dev/null || printf 'unknown')")"
  printf '%s' "$(doctor_check_json 1 1 "Runtime env file is readable." "" "$extra")"
}

source_checkout_check() {
  local hint=""
  local origin_url=""
  local repo_root=""
  local commit=""
  local extra=""

  if [[ ! -d "$INSTALL_DIR" ]]; then
    hint="Run \`$SCRIPT_NAME install\` to clone the source checkout."
    printf '%s' "$(doctor_check_json 1 0 "Source checkout directory is missing." "$hint" "\"path\":$(json_quote "$INSTALL_DIR")")"
    return 0
  fi

  repo_root="$(git_repo_root "$INSTALL_DIR" || true)"
  if [[ -z "$repo_root" ]]; then
    hint="Re-run \`$SCRIPT_NAME install --force\` to replace the install directory with a fresh clone."
    printf '%s' "$(doctor_check_json 1 0 "Install directory is not a Git repository." "$hint" "\"path\":$(json_quote "$INSTALL_DIR")")"
    return 0
  fi

  origin_url="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
  commit="$(git_current_commit "$INSTALL_DIR" 2>/dev/null || true)"
  extra="\"path\":$(json_quote "$INSTALL_DIR"),\"repo_root\":$(json_quote "$repo_root"),\"commit\":$(json_quote "$commit")"
  if [[ -n "$origin_url" ]]; then
    extra+=",\"origin_url\":$(json_quote "$origin_url")"
  fi

  if [[ -n "$REPO_URL" && -n "$origin_url" ]] && ! repo_urls_match "$REPO_URL" "$origin_url"; then
    hint="Expected origin \`$REPO_URL\`; reinstall with \`$SCRIPT_NAME install --force --repo-url ...\` if this checkout should be managed here."
    printf '%s' "$(doctor_check_json 1 0 "Source checkout origin does not match the expected repository." "$hint" "$extra")"
    return 0
  fi

  printf '%s' "$(doctor_check_json 1 1 "Source checkout is a Git repository." "" "$extra")"
}

path_visibility_check() {
  local ok=0

  if path_contains_dir "${BIN_DIR:-}" || path_contains_dir "$HOME/.local/bin"; then
    ok=1
  fi

  if (( ok )); then
    printf '%s' "$(doctor_check_json 0 1 "Launcher directory is present on PATH." "" "\"path\":$(json_quote "${BIN_DIR:-$HOME/.local/bin}")")"
  else
    printf '%s' "$(doctor_check_json 0 0 "Launcher directory is not present on PATH." "Add ${BIN_DIR:-$HOME/.local/bin} to PATH so generated launchers are easy to run." "\"path\":$(json_quote "${BIN_DIR:-$HOME/.local/bin}")")"
  fi
}

python_doctor_check() {
  local label="${1:?label is required}"
  local script_path="${2:?script path is required}"
  local active_gerrit_home_override="${3-}"
  local stdout_file=""
  local stderr_file=""
  local raw_stdout=""
  local raw_stderr=""
  local sanitized_stdout=""
  local sanitized_stderr=""
  local return_code=0
  local parsed=""
  local parsed_lines=()
  local ok_value=""
  local summary=""
  local hint=""
  local extra=""

  if [[ ! -f "$script_path" ]]; then
    hint="Make sure the cloned source tree contains $(basename "$script_path") and re-run install."
    printf '%s' "$(doctor_check_json 1 0 "$label doctor script is missing." "$hint" "\"path\":$(json_quote "$script_path")")"
    return 0
  fi

  stdout_file="$(mktemp)"
  stderr_file="$(mktemp)"
  (
    set -a
    if [[ -f "$CONFIG_FILE" ]]; then
      # shellcheck disable=SC1090
      source "$CONFIG_FILE"
    fi
    if [[ -n "$active_gerrit_home_override" ]]; then
      export ACTIVE_GERRIT_HOME="$active_gerrit_home_override"
    fi
    if [[ "$label" == "active-gerrit" ]]; then
      python3 "$script_path" doctor --json
    else
      python3 "$script_path" doctor
    fi
  ) >"$stdout_file" 2>"$stderr_file" || return_code=$?

  raw_stdout="$(cat "$stdout_file")"
  raw_stderr="$(cat "$stderr_file")"
  rm -f -- "$stdout_file" "$stderr_file"

  sanitized_stdout="$(redact_text "$raw_stdout")"
  sanitized_stderr="$(redact_text "$raw_stderr")"

  if [[ -z "$sanitized_stdout" ]]; then
    summary="$label doctor did not produce JSON output."
    if [[ -n "$sanitized_stderr" ]]; then
      summary="$summary stderr: $sanitized_stderr"
    fi
    hint="Inspect the Python stack trace above or run the Python doctor directly."
    extra="\"path\":$(json_quote "$script_path"),\"returncode\":$return_code"
    printf '%s' "$(doctor_check_json 1 0 "$summary" "$hint" "$extra")"
    return 0
  fi

  if ! parsed="$(python3 - <<'PY' "$sanitized_stdout" "$return_code" "$script_path" "$label" "$sanitized_stderr"
import json, sys

raw = sys.argv[1]
return_code = int(sys.argv[2])
script_path = sys.argv[3]
label = sys.argv[4]
stderr = sys.argv[5]

try:
    document = json.loads(raw)
except Exception as exc:  # noqa: BLE001
    print("parse_error")
    print(f"{label} doctor returned invalid JSON: {exc}")
    print("Inspect the Python doctor output directly.")
    print(f'"path":{json.dumps(script_path)},"returncode":{return_code},"stdout":{json.dumps(raw)},"stderr":{json.dumps(stderr)}')
    raise SystemExit(0)

ok = bool(document.get("ok"))
summary = ""
hint = ""
if ok:
    summary = f"{label} doctor completed successfully."
else:
    error = document.get("error") if isinstance(document.get("error"), dict) else {}
    summary = str(error.get("message") or f"{label} doctor reported a failure.")
    hint = str(error.get("hint") or "Inspect the nested doctor output for details.")

warnings = document.get("warnings")
details = json.dumps(document, ensure_ascii=False, sort_keys=True)
extra_parts = [
    f'"path":{json.dumps(script_path)}',
    f'"returncode":{return_code}',
    f'"details":{details}',
]
if stderr:
    extra_parts.append(f'"stderr":{json.dumps(stderr)}')
if isinstance(warnings, list):
    extra_parts.append(f'"warnings":{json.dumps(warnings, ensure_ascii=False)}')
print("ok" if ok else "failed")
print(summary)
print(hint)
print(",".join(extra_parts))
PY
)"; then
    :
  fi

  mapfile -t parsed_lines <<<"$parsed"
  ok_value="${parsed_lines[0]:-failed}"
  summary="${parsed_lines[1]:-$label doctor returned invalid output.}"
  hint="${parsed_lines[2]:-Inspect the nested doctor output for details.}"
  extra="${parsed_lines[3]:-\"path\":$(json_quote "$script_path"),\"returncode\":$return_code}"

  if [[ "$ok_value" == "ok" ]]; then
    printf '%s' "$(doctor_check_json 1 1 "$summary" "" "$extra")"
    return 0
  fi

  printf '%s' "$(doctor_check_json 1 0 "$summary" "$hint" "$extra")"
}

render_doctor_json() {
  local ok="${1:?ok flag is required}"
  local failed_checks_name="${2:?failed checks array name is required}"
  local warnings_name="${3:?warnings array name is required}"
  local dependencies_name="${4:?dependencies object entries are required}"
  local filesystem_name="${5:?filesystem object entries are required}"
  local source_name="${6:?source object entries are required}"
  local python_doctors_name="${7:?python doctor object entries are required}"
  local -n warnings_ref="$warnings_name"
  local meta_entries=()
  local data_entries=()
  local warning_entries=()
  local warnings_json=""
  local failed_json=""
  local warning=""

  meta_entries+=("\"fetched_at\":$(json_quote "$(timestamp_now_utc)")")
  meta_entries+=("\"install_dir\":$(json_quote "$INSTALL_DIR")")
  meta_entries+=("\"config_file\":$(json_quote "$CONFIG_FILE")")
  meta_entries+=("\"state_dir\":$(json_quote "$STATE_DIR")")
  meta_entries+=("\"cache_dir\":$(json_quote "$CACHE_DIR")")

  data_entries+=("\"dependencies\":$(json_object_from_entries "$dependencies_name")")
  data_entries+=("\"filesystem\":$(json_object_from_entries "$filesystem_name")")
  data_entries+=("\"source_checkout\":$(json_object_from_entries "$source_name")")
  data_entries+=("\"python_doctors\":$(json_object_from_entries "$python_doctors_name")")

  failed_json="$(json_array_from_entries "$failed_checks_name")"
  data_entries+=("\"doctor\":{\"ok\":$(json_bool "$ok"),\"failed_required_checks\":$failed_json}")
  for warning in "${warnings_ref[@]}"; do
    warning_entries+=("$(json_quote "$warning")")
  done
  warnings_json="$(json_array_from_entries warning_entries)"

  if [[ "$ok" == "1" ]]; then
    printf '{"ok":true,"command":"doctor","source":"installer","data":%s,"warnings":%s,"meta":%s}\n' \
      "$(json_object_from_entries data_entries)" \
      "$warnings_json" \
      "$(json_object_from_entries meta_entries)"
  else
    printf '{"ok":false,"command":"doctor","source":"installer","data":%s,"error":{"type":"DoctorFailed","message":"One or more required doctor checks failed.","hint":"Inspect data.doctor.failed_required_checks and the corresponding check summaries."},"warnings":%s,"meta":%s}\n' \
      "$(json_object_from_entries data_entries)" \
      "$warnings_json" \
      "$(json_object_from_entries meta_entries)"
  fi
}

render_doctor_human() {
  local ok="${1:?ok flag is required}"
  local warnings_name="${2:?warnings array name is required}"
  local lines_name="${3:?lines array name is required}"
  local -n warnings_ref="$warnings_name"
  local -n lines_ref="$lines_name"
  local line=""

  if [[ "$ok" == "1" ]]; then
    printf 'Installer doctor: PASS\n'
  else
    printf 'Installer doctor: FAIL\n'
  fi

  for line in "${lines_ref[@]}"; do
    printf '%s\n' "$line"
  done

  if ((${#warnings_ref[@]} > 0)); then
    printf 'Warnings:\n'
    for line in "${warnings_ref[@]}"; do
      printf '  - %s\n' "$line"
    done
  fi
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
  STATE_BIN_DIR="$BIN_DIR"
  STATE_PROFILE_PATH="$PROFILE_PATH"
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

  if directory_is_empty "$INSTALL_DIR"; then
    info "Cloning $REPO_URL@$REF into empty install directory $INSTALL_DIR"
    if ! clone_repo_to_dir "$REPO_URL" "$REF" "$INSTALL_DIR"; then
      die "Failed to clone \`$REPO_URL\` at ref \`$REF\`."
    fi
    refresh_install_state_from_checkout
    return 0
  fi

  ensure_existing_repo_matches
  refresh_install_state_from_checkout
}

restore_runtime_from_install_state() {
  read_install_state

  if [[ -z "${STATE_INSTALL_DIR:-}" ]]; then
    die "No install-state found at $INSTALL_STATE_FILE. Run \`$SCRIPT_NAME install\` first."
  fi

  apply_install_state_to_runtime
}

apply_install_state_to_runtime() {
  if [[ -z "${STATE_INSTALL_DIR:-}" ]]; then
    return 0
  fi

  INSTALL_DIR="${STATE_INSTALL_DIR:-$INSTALL_DIR}"
  CONFIG_FILE="${STATE_CONFIG_FILE:-$CONFIG_FILE}"
  CONFIG_DIR="$(dirname -- "$CONFIG_FILE")"
  INSTALL_STATE_FILE="$CONFIG_DIR/$DEFAULT_INSTALL_STATE_FILENAME"
  SKILL_DIR="${STATE_SKILL_DIR:-$SKILL_DIR}"
  SKILL_MODE="${STATE_SKILL_MODE:-$SKILL_MODE}"
  if (( ! BIN_DIR_WAS_SET )) && [[ -n "${STATE_BIN_DIR:-}" ]]; then
    BIN_DIR="$STATE_BIN_DIR"
  fi
  if (( ! PROFILE_WAS_SET )) && [[ -n "${STATE_PROFILE_PATH:-}" ]]; then
    PROFILE_PATH="$STATE_PROFILE_PATH"
  fi
  if [[ -n "${STATE_REPO_URL:-}" ]]; then
    REPO_URL="$STATE_REPO_URL"
  fi
  if [[ -n "${STATE_REF:-}" ]]; then
    REF="$STATE_REF"
  fi
  ACTIVE_GERRIT_HOME="$INSTALL_DIR/active-gerrit"
}

install_checkout_action_summary() {
  if [[ ! -e "$INSTALL_DIR" ]]; then
    printf '%s\n' 'clone source checkout into install_dir'
    return 0
  fi

  if (( FORCE )); then
    printf '%s\n' 'validate install_dir and back up conflicting content before replacing it'
  else
    printf '%s\n' 'validate existing install_dir and stop on conflicts unless --force is provided'
  fi
}

print_install_plan() {
  info "Install plan:"
  info "  source_repo=$REPO_URL"
  info "  source_ref=$REF"
  info "  install_dir=$INSTALL_DIR"
  info "  config_file=$CONFIG_FILE"
  info "  skill_dir=$SKILL_DIR"
  info "  skill_mode=$SKILL_MODE"
  info "  bin_dir=$BIN_DIR"
  if [[ "$PROFILE_PATH" == "/dev/null" ]]; then
    info "  profile_action=skip shell profile changes"
  elif [[ -n "$PROFILE_PATH" ]]; then
    info "  profile_action=update managed shell profile block at $PROFILE_PATH"
  else
    info "  profile_action=leave shell profile unchanged unless --profile is provided"
  fi
  if (( INSTALL_DEPS )); then
    info "  install_deps=enabled for explicit dependency guidance only; install.sh will not run sudo automatically"
  else
    info "  install_deps=disabled; install.sh will not install system packages unless --install-deps is explicitly provided"
  fi
  info "  checkout_action=$(install_checkout_action_summary)"
}

should_prompt_install_confirmation() {
  if (( NON_INTERACTIVE || ASSUME_YES )); then
    return 1
  fi

  if [[ -n "${CI:-}" ]]; then
    return 1
  fi

  if [[ -t 0 || -p /dev/stdin ]]; then
    return 0
  fi

  return 1
}

confirm_install_plan() {
  local choice=""

  if ! should_prompt_install_confirmation; then
    return 0
  fi

  choice="$(prompt_yes_no "Proceed with installation?" "yes")"
  if [[ "$choice" != "yes" ]]; then
    info "Installation cancelled by user."
    exit "$EXIT_SUCCESS"
  fi
}

emit_failure_next_steps() {
  local exit_code="${1:-$EXIT_FAILURE}"

  if (( FAILURE_NEXT_STEPS_EMITTED )) || (( exit_code == EXIT_SUCCESS )) || (( exit_code == EXIT_USAGE )); then
    return 0
  fi

  FAILURE_NEXT_STEPS_EMITTED=1
  error "Next steps:"
  case "$COMMAND" in
    install)
      error "  Fix the prerequisite or path issue above, then re-run $SCRIPT_NAME install --verbose"
      error "  Run $SCRIPT_NAME doctor after the checkout is ready."
      ;;
    config)
      error "  Review $CONFIG_FILE and re-run $SCRIPT_NAME config if any Gerrit settings need to change."
      ;;
    deploy-skill)
      error "  Inspect the target paths above and only re-run with --force if the existing content can be backed up safely."
      ;;
    update)
      error "  Inspect the checkout state above, then re-run $SCRIPT_NAME status or $SCRIPT_NAME doctor after fixing the issue."
      ;;
    doctor)
      error "  Address the failing checks above and re-run $SCRIPT_NAME doctor --json for machine-readable diagnostics if needed."
      ;;
    uninstall)
      error "  Review the uninstall plan above; no files are removed by default."
      ;;
    *)
      error "  Re-run with --verbose for more detail once the issue above is fixed."
      ;;
  esac
}

handle_script_exit() {
  local exit_code="${1:-0}"
  emit_failure_next_steps "$exit_code"
}

git_worktree_is_clean() {
  local repo_dir="${1:?repository path is required}"
  [[ -z "$(git -C "$repo_dir" status --porcelain 2>/dev/null)" ]]
}

git_resolve_branch_target() {
  local repo_dir="${1:?repository path is required}"
  local ref="${2:?ref is required}"
  git -C "$repo_dir" rev-parse "origin/$ref"
}

git_resolve_ref_target() {
  local repo_dir="${1:?repository path is required}"
  local ref="${2:?ref is required}"
  git -C "$repo_dir" rev-parse "${ref}^{commit}"
}

git_has_remote_branch() {
  local repo_dir="${1:?repository path is required}"
  local ref="${2:?ref is required}"
  git -C "$repo_dir" show-ref --verify --quiet "refs/remotes/origin/$ref"
}

ensure_local_branch_for_update() {
  local repo_dir="${1:?repository path is required}"
  local ref="${2:?ref is required}"

  if git -C "$repo_dir" show-ref --verify --quiet "refs/heads/$ref"; then
    git -C "$repo_dir" checkout "$ref"
  else
    git -C "$repo_dir" checkout -b "$ref" --track "origin/$ref"
  fi
}

print_update_restore_instructions() {
  local previous_head="${1:?previous head is required}"
  FAILURE_NEXT_STEPS_EMITTED=1
  error "Update failed after starting from commit $previous_head."
  error "To restore the previous checkout manually, run:"
  error "  cd $INSTALL_DIR"
  error "  git checkout $previous_head"
  error "  $SCRIPT_NAME deploy-skill"
  error "  $SCRIPT_NAME doctor"
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

  print_install_plan
  confirm_install_plan

  sync_source_checkout
  info "Source checkout is ready."
  warn "Config generation and Skill deployment are still pending follow-up tasks. Run \`$SCRIPT_NAME doctor\` to verify the installation."
}

handle_doctor() {
  local dependencies=()
  # shellcheck disable=SC2034
  local filesystem=()
  # shellcheck disable=SC2034
  local source_checkout=()
  # shellcheck disable=SC2034
  local python_doctors=()
  local warnings=()
  local human_lines=()
  # shellcheck disable=SC2034
  local failed_checks=()
  local section_name=""
  local entry=""
  local key=""
  local value=""
  local status="PASS"
  local summary=""
  local hint=""
  local overall_ok=1
  local python_ok=0
  local active_gerrit_script=""
  local workflow_script=""

  if (($# > 0)); then
    warn "Command \`doctor\` ignores reserved arguments: $*"
  fi

  append_object_entry dependencies "bash" "$(doctor_check_json 1 1 "Installer is running under Bash." "" "\"version\":$(json_quote "${BASH_VERSION:-unknown}")")"
  append_object_entry dependencies "git" "$(command_check "git" 1 "--version")"
  append_object_entry dependencies "python3" "$(python_version_check)"
  append_object_entry dependencies "curl_or_wget" "$(curl_or_wget_check)"
  append_object_entry dependencies "sed" "$(command_check "sed" 1 "--version")"
  append_object_entry dependencies "jq" "$(command_check "jq" 0 "--version")"
  append_object_entry dependencies "openssl" "$(command_check "openssl" 0 "version")"
  append_object_entry dependencies "ssh" "$(command_check "ssh" 0 "-V")"
  append_object_entry dependencies "rg" "$(command_check "rg" 0 "--version")"
  append_object_entry dependencies "shellcheck" "$(command_check "shellcheck" 0 "--version")"
  append_object_entry dependencies "bats" "$(command_check "bats" 0 "--version")"

  append_object_entry filesystem "install_dir" "$(directory_access_check "$INSTALL_DIR" 1 "Install directory" 1)"
  append_object_entry filesystem "config_dir" "$(directory_access_check "$CONFIG_DIR" 1 "Config directory" 1)"
  append_object_entry filesystem "cache_dir" "$(directory_access_check "$CACHE_DIR" 1 "Cache directory" 0)"
  append_object_entry filesystem "state_dir" "$(directory_access_check "$STATE_DIR" 1 "State directory" 0)"
  append_object_entry filesystem "config_file" "$(config_file_check)"
  append_object_entry filesystem "path" "$(path_visibility_check)"

  append_object_entry source_checkout "checkout" "$(source_checkout_check)"

  if [[ "${dependencies[2]#*:}" == *'"ok":true'* ]]; then
    python_ok=1
  fi

  if (( python_ok )); then
    active_gerrit_script="$INSTALL_DIR/active-gerrit/scripts/gerrit_cli.py"
    workflow_script="$INSTALL_DIR/active-gerrit-workflow/scripts/workflow_cli.py"
    append_object_entry python_doctors "active_gerrit" "$(python_doctor_check "active-gerrit" "$active_gerrit_script")"
    append_object_entry python_doctors "workflow" "$(python_doctor_check "workflow" "$workflow_script" "$INSTALL_DIR/active-gerrit")"
  else
    append_object_entry python_doctors "active_gerrit" "$(doctor_check_json 1 0 "Skipped active-gerrit doctor because Python 3.9+ is unavailable." "Install Python 3.9+ and re-run doctor.")"
    append_object_entry python_doctors "workflow" "$(doctor_check_json 1 0 "Skipped workflow doctor because Python 3.9+ is unavailable." "Install Python 3.9+ and re-run doctor.")"
  fi

  for section_name in dependencies filesystem source_checkout python_doctors; do
    local -n section_ref="$section_name"
    human_lines+=("${section_name}:")
    for entry in "${section_ref[@]}"; do
      key="$(redact_text "${entry%%:*}")"
      key="${key#\"}"
      key="${key%\"}"
      value="${entry#*:}"
      if [[ "$value" == *'"required":true'* && "$value" == *'"ok":false'* ]]; then
        overall_ok=0
        append_json_string failed_checks "$section_name.$key"
      fi

      if [[ "$value" == *'"ok":true'* ]]; then
        status="PASS"
      elif [[ "$value" == *'"required":true'* ]]; then
        status="FAIL"
      else
        status="WARN"
        summary="$(json_string_field "$value" "summary" || true)"
        if [[ -n "$summary" ]]; then
          warnings+=("$section_name.$key: $summary")
        fi
      fi

      summary="$(json_string_field "$value" "summary" || true)"
      hint="$(json_string_field "$value" "hint" || true)"
      if [[ -n "$summary" ]]; then
        human_lines+=("  [$status] $key: $summary")
      else
        human_lines+=("  [$status] $key")
      fi
      if [[ -n "$hint" ]]; then
        human_lines+=("         hint: $hint")
      fi
    done
  done

  if (( OUTPUT_JSON )); then
    render_doctor_json "$overall_ok" failed_checks warnings dependencies filesystem source_checkout python_doctors
  else
    render_doctor_human "$overall_ok" warnings human_lines
  fi

  if (( overall_ok )); then
    return "$EXIT_SUCCESS"
  fi
  return "$EXIT_FAILURE"
}

handle_config() {
  local base_url=""
  local auth_type=""
  local username=""
  local password=""
  local verify_ssl=""
  local timeout_seconds=""
  local default_notify=""
  local gerrit_cache_dir=""
  local save_password_choice="yes"
  local save_password_flag=0
  local secret_input=""
  local had_existing_password=0
  local env_content=""

  if (($# > 0)); then
    warn "Command \`config\` ignores reserved arguments: $*"
  fi

  read_existing_runtime_config
  print_config_intro

  base_url="$(config_value_or_default "${GERRIT_BASE_URL:-}" "$EXISTING_GERRIT_BASE_URL" "")"
  auth_type="$(config_value_or_default "${GERRIT_AUTH_TYPE:-}" "$EXISTING_GERRIT_AUTH_TYPE" "basic")"
  username="$(config_value_or_default "${GERRIT_USERNAME:-}" "$EXISTING_GERRIT_USERNAME" "")"
  password="$(config_value_or_default "${GERRIT_HTTP_PASSWORD:-}" "$EXISTING_GERRIT_HTTP_PASSWORD" "")"
  verify_ssl="$(config_value_or_default "${GERRIT_VERIFY_SSL:-}" "$EXISTING_GERRIT_VERIFY_SSL" "true")"
  timeout_seconds="$(config_value_or_default "${GERRIT_TIMEOUT_SECONDS:-}" "$EXISTING_GERRIT_TIMEOUT_SECONDS" "30")"
  default_notify="$(config_value_or_default "${GERRIT_DEFAULT_NOTIFY:-}" "$EXISTING_GERRIT_DEFAULT_NOTIFY" "OWNER_REVIEWERS")"
  gerrit_cache_dir="$(config_value_or_default "${GERRIT_CACHE_DIR:-}" "$EXISTING_GERRIT_CACHE_DIR" "$(default_gerrit_cache_dir)")"

  if [[ -n "$EXISTING_GERRIT_HTTP_PASSWORD" || -n "${GERRIT_HTTP_PASSWORD:-}" ]]; then
    had_existing_password=1
    save_password_choice="yes"
  fi

  if (( NON_INTERACTIVE )); then
    if [[ -z "${GERRIT_BASE_URL:-}" ]]; then
      die "NONINTERACTIVE=1 requires GERRIT_BASE_URL to be set in the environment." "$EXIT_USAGE"
    fi
    if [[ -z "${GERRIT_USERNAME:-}" ]]; then
      die "NONINTERACTIVE=1 requires GERRIT_USERNAME to be set in the environment." "$EXIT_USAGE"
    fi
    base_url="${GERRIT_BASE_URL:-}"
    username="${GERRIT_USERNAME:-}"
    auth_type="basic"
    if [[ -n "${GERRIT_HTTP_PASSWORD:-}" ]]; then
      password="${GERRIT_HTTP_PASSWORD:-}"
      save_password_flag=1
    else
      password=""
      save_password_flag=0
    fi
  else
    while true; do
      base_url="$(prompt_with_default "Gerrit base URL" "$base_url")"
      if validate_http_url "$base_url"; then
        break
      fi
      warn "Please enter an http:// or https:// URL."
    done

    while true; do
      username="$(prompt_with_default "Gerrit username" "$username")"
      if [[ -n "$username" ]]; then
        break
      fi
      warn "Gerrit username is required."
    done

    save_password_choice="$(prompt_yes_no "Save Gerrit HTTP password to $CONFIG_FILE?" "$save_password_choice")"
    if [[ "$save_password_choice" == "yes" ]]; then
      if [[ -n "$password" ]]; then
        had_existing_password=1
      fi
      secret_input="$(prompt_secret "Gerrit HTTP password" 0 "$had_existing_password")"
      if [[ -n "$secret_input" ]]; then
        password="$secret_input"
      fi
      if [[ -z "$password" ]]; then
        die "Gerrit HTTP password cannot be blank when saving it to the config file." "$EXIT_USAGE"
      fi
      save_password_flag=1
    else
      password=""
      save_password_flag=0
    fi

    while true; do
      verify_ssl="$(prompt_with_default "Verify TLS certificates (true/false)" "$verify_ssl")"
      verify_ssl="${verify_ssl,,}"
      if validate_boolean_string "$verify_ssl"; then
        break
      fi
      warn "Please enter \`true\` or \`false\`."
    done

    while true; do
      timeout_seconds="$(prompt_with_default "HTTP timeout seconds" "$timeout_seconds")"
      if validate_positive_integer "$timeout_seconds"; then
        break
      fi
      warn "Please enter a positive integer."
    done

    default_notify="$(prompt_with_default "Default Gerrit notify policy" "$default_notify")"
    gerrit_cache_dir="$(prompt_with_default "Gerrit cache directory" "$gerrit_cache_dir")"
  fi

  auth_type="basic"

  if ! validate_http_url "$base_url"; then
    die "GERRIT_BASE_URL must start with http:// or https://." "$EXIT_USAGE"
  fi
  if [[ -z "$username" ]]; then
    die "GERRIT_USERNAME is required." "$EXIT_USAGE"
  fi
  if ! validate_boolean_string "$verify_ssl"; then
    die "GERRIT_VERIFY_SSL must be \`true\` or \`false\`." "$EXIT_USAGE"
  fi
  if ! validate_positive_integer "$timeout_seconds"; then
    die "GERRIT_TIMEOUT_SECONDS must be a positive integer." "$EXIT_USAGE"
  fi

  ensure_private_dir "$CONFIG_DIR"
  ensure_dir "$CACHE_DIR"
  ensure_dir "$(dirname -- "$gerrit_cache_dir")"

  if [[ -f "$CONFIG_FILE" ]]; then
    backup_existing_path "$CONFIG_FILE"
  fi

  env_content="$(render_runtime_env_file \
    "$base_url" \
    "$auth_type" \
    "$username" \
    "$password" \
    "$save_password_flag" \
    "$verify_ssl" \
    "$timeout_seconds" \
    "$default_notify" \
    "$gerrit_cache_dir")"

  atomic_write_file "$CONFIG_FILE" 600 "$env_content"
  set_private_file_mode "$CONFIG_FILE"

  ensure_runtime_launchers
  maybe_update_profile

  STATE_INSTALL_DIR="$INSTALL_DIR"
  STATE_CONFIG_FILE="$CONFIG_FILE"
  STATE_SKILL_DIR="$SKILL_DIR"
  STATE_SKILL_MODE="$SKILL_MODE"
  STATE_BIN_DIR="$BIN_DIR"
  STATE_PROFILE_PATH="$PROFILE_PATH"
  if [[ -z "${STATE_REPO_URL:-}" || "$REPO_URL" != "$DEFAULT_REPO_URL" ]]; then
    STATE_REPO_URL="$REPO_URL"
  fi
  if [[ -z "${STATE_REF:-}" || "$REF" != "$DEFAULT_REF" ]]; then
    STATE_REF="$REF"
  fi
  atomic_write_file "$INSTALL_STATE_FILE" 600 "$(render_install_state)"

  info "Wrote Gerrit runtime config to $CONFIG_FILE"
  info "  GERRIT_BASE_URL=$base_url"
  info "  GERRIT_AUTH_TYPE=$auth_type"
  info "  GERRIT_USERNAME=$username"
  if (( save_password_flag )); then
    info "  GERRIT_HTTP_PASSWORD=<redacted>"
  else
    info "  GERRIT_HTTP_PASSWORD=<redacted> (not saved to file)"
  fi
  info "  GERRIT_VERIFY_SSL=$verify_ssl"
  info "  GERRIT_TIMEOUT_SECONDS=$timeout_seconds"
  info "  GERRIT_DEFAULT_NOTIFY=$default_notify"
  info "  GERRIT_CACHE_DIR=$gerrit_cache_dir"
}

handle_deploy_skill() {
  local skill_name=""

  if (($# > 0)); then
    warn "Command \`deploy-skill\` ignores reserved arguments: $*"
  fi

  ensure_dir "$SKILL_DIR"

  for skill_name in "${SKILL_NAMES[@]}"; do
    if [[ "$SKILL_MODE" == "symlink" ]]; then
      deploy_skill_symlink "$skill_name"
    else
      deploy_skill_copy "$skill_name"
    fi
  done

  ensure_runtime_launchers
  maybe_update_profile

  STATE_INSTALL_DIR="$INSTALL_DIR"
  STATE_CONFIG_FILE="$CONFIG_FILE"
  STATE_SKILL_DIR="$SKILL_DIR"
  STATE_SKILL_MODE="$SKILL_MODE"
  STATE_BIN_DIR="$BIN_DIR"
  STATE_PROFILE_PATH="$PROFILE_PATH"
  if [[ -z "${STATE_REPO_URL:-}" || "$REPO_URL" != "$DEFAULT_REPO_URL" ]]; then
    STATE_REPO_URL="$REPO_URL"
  fi
  if [[ -d "$INSTALL_DIR" ]] && git_repo_root "$INSTALL_DIR" >/dev/null 2>&1; then
    STATE_REF="$(git_current_checkout_ref "$INSTALL_DIR")"
    STATE_INSTALLED_COMMIT="$(git_current_commit "$INSTALL_DIR")"
    STATE_INSTALLED_AT="$(timestamp_now_utc)"
  fi
  atomic_write_file "$INSTALL_STATE_FILE" 600 "$(render_install_state)"

  info "Skill deployment completed:"
  info "  skill_dir=$SKILL_DIR"
  info "  skill_mode=$SKILL_MODE"
}

handle_update() {
  local previous_head=""
  local current_head=""
  local target_head=""
  local update_mode="detached"
  local changed=0

  if (($# > 0)); then
    warn "Command \`update\` ignores reserved arguments: $*"
  fi

  restore_runtime_from_install_state
  require_command git
  require_command python3

  if [[ ! -d "$INSTALL_DIR" ]]; then
    die "Install directory does not exist: $INSTALL_DIR"
  fi
  if ! git_repo_root "$INSTALL_DIR" >/dev/null 2>&1; then
    die "Install directory is not a Git repository: $INSTALL_DIR"
  fi
  if ! git_worktree_is_clean "$INSTALL_DIR"; then
    die "Working tree is dirty at $INSTALL_DIR. Commit or stash your changes before running update."
  fi

  ensure_existing_repo_matches

  previous_head="$(git_current_commit "$INSTALL_DIR")"
  info "Updating source checkout at $INSTALL_DIR"
  info "  previous_head=$previous_head"
  git -C "$INSTALL_DIR" fetch --tags --prune origin

  if git_has_remote_branch "$INSTALL_DIR" "$REF"; then
    update_mode="branch"
    target_head="$(git_resolve_branch_target "$INSTALL_DIR" "$REF")"
    ensure_local_branch_for_update "$INSTALL_DIR" "$REF"
    if [[ "$previous_head" != "$target_head" ]]; then
      info "Fast-forwarding branch \`$REF\` to $target_head"
    else
      info "Branch \`$REF\` is already up to date."
    fi
    if ! git -C "$INSTALL_DIR" pull --ff-only origin "$REF"; then
      print_update_restore_instructions "$previous_head"
      return "$EXIT_FAILURE"
    fi
  else
    target_head="$(git_resolve_ref_target "$INSTALL_DIR" "$REF" 2>/dev/null || true)"
    if [[ -z "$target_head" ]]; then
      die "Could not resolve update ref \`$REF\` after fetching origin."
    fi
    if [[ "$previous_head" != "$target_head" ]]; then
      info "Checking out detached ref \`$REF\` at $target_head"
      if ! git -C "$INSTALL_DIR" checkout --detach "$REF"; then
        print_update_restore_instructions "$previous_head"
        return "$EXIT_FAILURE"
      fi
    else
      info "Detached ref \`$REF\` is already current."
      if ! git -C "$INSTALL_DIR" checkout --detach "$REF" >/dev/null 2>&1; then
        print_update_restore_instructions "$previous_head"
        return "$EXIT_FAILURE"
      fi
    fi
  fi

  current_head="$(git_current_commit "$INSTALL_DIR")"
  if [[ "$current_head" != "$previous_head" ]]; then
    changed=1
    info "Updated source checkout to $current_head"
  else
    info "No source changes were applied."
  fi

  if ! handle_deploy_skill; then
    print_update_restore_instructions "$previous_head"
    return "$EXIT_FAILURE"
  fi

  if ! handle_doctor; then
    print_update_restore_instructions "$previous_head"
    return "$EXIT_FAILURE"
  fi

  refresh_install_state_from_checkout
  info "Update completed:"
  info "  mode=$update_mode"
  info "  previous_head=$previous_head"
  info "  current_head=$current_head"
  if (( changed )); then
    info "  source_changed=true"
  else
    info "  source_changed=false"
  fi
}

handle_status() {
  if (($# > 0)); then
    warn "Command \`status\` ignores reserved arguments: $*"
  fi

  read_install_state
  apply_install_state_to_runtime

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
  printf 'bin_dir=%s\n' "$BIN_DIR"
  printf 'profile_path=%s\n' "$PROFILE_PATH"
  printf 'state_install_dir=%s\n' "${STATE_INSTALL_DIR:-}"
  printf 'state_config_file=%s\n' "${STATE_CONFIG_FILE:-}"
  printf 'state_skill_dir=%s\n' "${STATE_SKILL_DIR:-}"
  printf 'state_skill_mode=%s\n' "${STATE_SKILL_MODE:-}"
  printf 'state_bin_dir=%s\n' "${STATE_BIN_DIR:-}"
  printf 'state_profile_path=%s\n' "${STATE_PROFILE_PATH:-}"
  printf 'state_repo_url=%s\n' "${STATE_REPO_URL:-}"
  printf 'state_ref=%s\n' "${STATE_REF:-}"
  printf 'state_installed_commit=%s\n' "${STATE_INSTALLED_COMMIT:-}"
  printf 'state_installed_at=%s\n' "${STATE_INSTALLED_AT:-}"
}

handle_uninstall() {
  if (($# > 0)); then
    warn "Command \`uninstall\` ignores reserved arguments: $*"
  fi

  read_install_state
  apply_install_state_to_runtime

  printf 'action=%s\n' 'plan-only'
  printf 'delete_performed=%s\n' 'false'
  printf 'install_dir=%s\n' "$INSTALL_DIR"
  printf 'config_file=%s\n' "$CONFIG_FILE"
  printf 'cache_dir=%s\n' "$CACHE_DIR"
  printf 'state_dir=%s\n' "$STATE_DIR"
  printf 'skill_dir=%s\n' "$SKILL_DIR"
  printf 'skill_active_gerrit=%s\n' "$(skill_target_dir "active-gerrit")"
  printf 'skill_workflow=%s\n' "$(skill_target_dir "active-gerrit-workflow")"
  printf 'profile_path=%s\n' "$PROFILE_PATH"
  printf 'remove_profile_block=%s\n' "$( [[ -n "$PROFILE_PATH" && "$PROFILE_PATH" != "/dev/null" ]] && printf 'manual-review' || printf 'false' )"
  printf 'remove_config_by_default=%s\n' 'false'
  printf 'remove_cache_by_default=%s\n' 'false'
  printf 'remove_state_by_default=%s\n' 'false'
  warn "Uninstall is currently plan-only. No files were removed."
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

trap 'handle_script_exit $?' EXIT

main "$@"
