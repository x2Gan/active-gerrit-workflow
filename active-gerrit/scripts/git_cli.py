#!/usr/bin/env python3
"""CLI entry point for active-gerrit local Git tools."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from git_gerrit import build_review_ref, resolve_change_ref, select_gerrit_remote
from git_runner import GitCommandError, GitConfigError, GitError, GitExecutableNotFound, GitRunner, GitRunnerConfig
from git_schemas import (
    EXIT_CONFIG,
    EXIT_FAILURE,
    EXIT_SUCCESS,
    EXIT_USAGE,
    command_name,
    error_envelope,
    fallback_args,
    print_json,
    success_envelope,
)

CLI_NAME = "active-gerrit-git"
CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
RECORD_SEPARATOR = "\x1e"
FIELD_SEPARATOR = "\x1f"
CONFIG_KEYS = (
    "user.name",
    "user.email",
    "core.hooksPath",
    "remote.pushDefault",
    "push.default",
)
DIFF_STATUS_LABELS = {
    "A": "added",
    "C": "copied",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type_changed",
    "U": "unmerged",
    "X": "unknown",
}
DEFAULT_FETCH_TIMEOUT_SECONDS = 180.0
DEFAULT_PUSH_TIMEOUT_SECONDS = 300.0
CHANGE_ID_TRAILER_RE = re.compile(r"(?im)^Change-Id:\s*(?P<value>\S+)\s*$")
VALID_CHANGE_ID_RE = re.compile(r"^I[0-9A-Fa-f]{4,}$")

PLANNED_COMMANDS = (
    ("git-doctor", "Check local Git availability, repository configuration, remotes, and hooks."),
    ("repo-info", "Describe the current Git repository, branch, upstream, HEAD, and remotes."),
    ("repo-status", "Read working tree status using machine-readable Git output."),
    ("repo-remotes", "List configured Git remotes with credential redaction."),
    ("repo-config", "Read Gerrit-relevant local Git configuration."),
    ("repo-diff", "Summarize local staged or unstaged changes."),
    ("repo-diff-file", "Read a single-file local Git diff."),
    ("repo-log", "Read structured recent commit summaries."),
    ("repo-show", "Read one structured commit summary."),
    ("repo-branches", "List local and remote branches."),
    ("fetch-change", "Fetch a Gerrit patch set ref into the local repository."),
    ("checkout-change", "Check out a fetched Gerrit patch set safely."),
    ("worktree-change", "Create a dedicated worktree for a Gerrit patch set."),
    ("change-id-check", "Check Change-Id trailers for HEAD or a commit message file."),
    ("commit-plan", "Summarize files and metadata before creating or amending a commit."),
    ("commit-create", "Create a commit from explicit paths."),
    ("commit-amend", "Amend the current commit while preserving the Gerrit Change-Id by default."),
    ("push-review-plan", "Build a Gerrit refs/for push plan without updating the remote."),
    ("push-review", "Dry-run or execute a Gerrit review push."),
)


class CLIUsageError(Exception):
    """Argument or command usage error."""


def script_path() -> Path:
    return Path(__file__).resolve()


def default_active_gerrit_home() -> Path:
    return script_path().parents[1]


def configured_active_gerrit_home(env: Mapping[str, str]) -> Path:
    configured = (env.get("ACTIVE_GERRIT_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return default_active_gerrit_home().resolve(strict=False)


def resolve_active_gerrit_cli(home: Path) -> Path:
    candidates = [home / "scripts" / "gerrit_cli.py"]
    if home.name != "active-gerrit":
        candidates.append(home / "active-gerrit" / "scripts" / "gerrit_cli.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise GitConfigError(
        f"Could not find active-gerrit/scripts/gerrit_cli.py under {home}. "
        "Set ACTIVE_GERRIT_HOME to the active-gerrit directory or repo root."
    )


def build_gerrit_cli_command(args: argparse.Namespace, env: Mapping[str, str], command: str, extra_args: Sequence[str]) -> Sequence[str]:
    invocation = [
        env.get("PYTHON_BIN") or sys.executable,
        str(resolve_active_gerrit_cli(configured_active_gerrit_home(env))),
    ]
    if getattr(args, "trace", None):
        invocation.extend(["--trace", str(args.trace)])
    invocation.append(command)
    invocation.extend(extra_args)
    return invocation


def run_gerrit_cli_command(
    args: argparse.Namespace,
    env: Mapping[str, str],
    command: str,
    extra_args: Sequence[str],
    *,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    invocation = build_gerrit_cli_command(args, env, command, extra_args)
    timeout = timeout_seconds if timeout_seconds is not None else GitRunnerConfig.from_args_env(args, env).timeout_seconds
    try:
        completed = subprocess.run(
            invocation,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=dict(env),
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitConfigError(f"Python executable for active-gerrit is not available: {invocation[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(
            f"active-gerrit {command} timed out after {timeout} seconds.",
            args=invocation,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
        ) from exc
    except OSError as exc:
        raise GitConfigError(f"Could not execute active-gerrit {command}: {exc}") from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if not stdout:
        message = f"active-gerrit {command} produced no JSON output."
        if stderr:
            message = f"{message} stderr={stderr}"
        raise GitCommandError(message, args=invocation, returncode=completed.returncode, stderr=stderr)

    try:
        document = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise GitCommandError(f"active-gerrit {command} returned invalid JSON: {exc}", args=invocation, stderr=stderr) from exc

    if not document.get("ok"):
        error = document.get("error") if isinstance(document.get("error"), Mapping) else {}
        message = error.get("message") or f"active-gerrit {command} failed."
        hint = error.get("hint")
        if hint:
            message = f"{message} Hint: {hint}"
        raise GitCommandError(str(message), args=invocation, returncode=completed.returncode, stderr=stderr)

    return document


def coerce_fetch_timeout(value: object, env: Mapping[str, str], default_timeout: float) -> float:
    raw = value if value is not None else env.get("GIT_FETCH_TIMEOUT_SECONDS")
    if raw is None or raw == "":
        return max(default_timeout, DEFAULT_FETCH_TIMEOUT_SECONDS)
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise GitConfigError("GIT_FETCH_TIMEOUT_SECONDS or --fetch-timeout must be numeric.") from exc
    if timeout <= 0:
        raise GitConfigError("GIT_FETCH_TIMEOUT_SECONDS or --fetch-timeout must be greater than zero.")
    return timeout


def normalize_branch_token(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "change"
    token = []
    for ch in text:
        if ch.isalnum() or ch in {"-", "_", "/"}:
            token.append(ch)
        else:
            token.append("-")
    normalized = "".join(token).strip("-/_")
    return normalized or "change"


def default_review_branch(change_document: Mapping[str, Any], change_arg: str, ref_info: Mapping[str, Any]) -> str:
    summary = change_document.get("summary") if isinstance(change_document.get("summary"), Mapping) else change_document
    number = summary.get("number") or summary.get("_number")
    patch_set = ref_info.get("patch_set")
    change_part = normalize_branch_token(number if number is not None else change_arg.split("~")[-1])
    patch_part = normalize_branch_token(patch_set if patch_set is not None else ref_info.get("requested_revision") or "current")
    return f"review/{change_part}-{patch_part}"


def default_worktree_path(repo_root: Path, branch_name: str) -> Path:
    return repo_root.parent / f"{repo_root.name}-{normalize_branch_token(branch_name).replace('/', '-')}"


def ensure_branch_absent(runner: GitRunner, repo_root: Path, branch_name: str) -> None:
    exists = runner.run(("show-ref", "--verify", f"refs/heads/{branch_name}"), cwd=repo_root, check=False)
    if exists.returncode == 0:
        raise GitCommandError(
            f"Branch {branch_name} already exists. Choose --branch explicitly or delete/reuse it manually.",
            args=("show-ref", "--verify", f"refs/heads/{branch_name}"),
            returncode=0,
        )


def fetch_change_revision(
    runner: GitRunner,
    repo: Mapping[str, Any],
    args: argparse.Namespace,
    env: Mapping[str, str],
) -> Dict[str, Any]:
    fetch_timeout = coerce_fetch_timeout(getattr(args, "fetch_timeout", None), env, runner.config.timeout_seconds)
    change_document = run_gerrit_cli_command(
        args,
        env,
        "get-change",
        ("--change", args.change, "--detail", "detail"),
        timeout_seconds=fetch_timeout,
    ).get("data")
    if not isinstance(change_document, Mapping):
        raise GitCommandError("active-gerrit get-change returned an unexpected payload.")

    ref_info = resolve_change_ref(change_document, revision=args.revision)
    summary = change_document.get("summary") if isinstance(change_document.get("summary"), Mapping) else change_document
    project = summary.get("project")

    selected_remote = select_gerrit_remote(
        repo.get("remotes") or [],
        explicit_remote=getattr(args, "remote", None),
        env=env,
        project=str(project) if project else None,
    )
    remote_name = selected_remote.get("name")
    if not isinstance(remote_name, str) or not remote_name:
        raise GitCommandError("Could not resolve a Gerrit remote name from local repository remotes.")

    fetch_result = runner.run(("fetch", remote_name, str(ref_info["ref"])), cwd=Path(repo["repo_root"]), timeout=fetch_timeout)
    fetched_commit_result = runner.run(("rev-parse", "--verify", "FETCH_HEAD"), cwd=Path(repo["repo_root"]))
    fetched_commit = fetched_commit_result.stdout.strip()
    subject_result = runner.run(("show", "-s", "--format=%s", "FETCH_HEAD"), cwd=Path(repo["repo_root"]), check=False)

    return {
        "change": args.change,
        "change_number": summary.get("number") or summary.get("_number"),
        "requested_revision": args.revision,
        "resolved_revision": ref_info.get("revision"),
        "patch_set": ref_info.get("patch_set"),
        "ref": ref_info.get("ref"),
        "ref_source": ref_info.get("source"),
        "project": project,
        "branch": summary.get("branch"),
        "remote": remote_name,
        "remote_reason": selected_remote.get("reason"),
        "remote_warnings": list(selected_remote.get("warnings") or []),
        "fetch": {
            "stdout": fetch_result.stdout,
            "stderr": fetch_result.stderr,
            "stdout_truncated": fetch_result.stdout_truncated,
            "stderr_truncated": fetch_result.stderr_truncated,
            "timeout_seconds": fetch_timeout,
        },
        "fetched_commit": fetched_commit,
        "fetched_subject": subject_result.stdout.strip() or None,
    }


def enforce_clean_worktree_or_fail(repo: Mapping[str, Any], *, message: Optional[str] = None) -> None:
    status = repo.get("status") if isinstance(repo.get("status"), Mapping) else {}
    if status.get("is_clean"):
        return
    raise GitCommandError(
        message
        or (
            "Working tree is not clean. Use worktree-change to avoid touching your current workspace, "
            "or commit/stash local changes first."
        ),
    )


def coerce_push_timeout(value: object, env: Mapping[str, str], default_timeout: float) -> float:
    raw = value if value is not None else env.get("GIT_PUSH_TIMEOUT_SECONDS")
    if raw is None or raw == "":
        return max(default_timeout, DEFAULT_PUSH_TIMEOUT_SECONDS)
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise GitConfigError("GIT_PUSH_TIMEOUT_SECONDS or --push-timeout must be numeric.") from exc
    if timeout <= 0:
        raise GitConfigError("GIT_PUSH_TIMEOUT_SECONDS or --push-timeout must be greater than zero.")
    return timeout


def short_branch_name(ref_name: object) -> Optional[str]:
    text = str(ref_name or "").strip()
    if not text:
        return None
    if text.startswith("refs/heads/"):
        return text[len("refs/heads/") :]
    return text.lstrip("/") or None


def resolve_review_branch(
    args: argparse.Namespace,
    env: Mapping[str, str],
    repo: Mapping[str, Any],
    config_map: Mapping[str, Sequence[str]],
) -> Dict[str, Any]:
    explicit_branch = (getattr(args, "branch", None) or "").strip()
    if explicit_branch:
        return {"branch": explicit_branch, "source": "explicit_arg"}

    env_branch = (env.get("GERRIT_REVIEW_BRANCH") or "").strip()
    if env_branch:
        return {"branch": env_branch, "source": "env_review_branch"}

    current_branch = repo.get("current_branch")
    if isinstance(current_branch, str) and current_branch:
        merge_ref = config_value(config_map, f"branch.{current_branch}.merge")
        merge_branch = short_branch_name(merge_ref)
        if merge_branch:
            return {"branch": merge_branch, "source": "branch_merge"}

    upstream = repo.get("upstream")
    if isinstance(upstream, str) and "/" in upstream:
        return {"branch": upstream.split("/", 1)[1], "source": "upstream_short"}

    if isinstance(current_branch, str) and current_branch:
        return {"branch": current_branch, "source": "current_branch"}

    raise CLIUsageError(
        "Could not determine the Gerrit review target branch. Pass --branch, set GERRIT_REVIEW_BRANCH, "
        "or configure branch.<name>.merge for the current branch."
    )


def review_ref_options_from_args(args: argparse.Namespace) -> Dict[str, object]:
    return {
        "topic": getattr(args, "topic", None),
        "hashtag": list(getattr(args, "hashtag", []) or []),
        "reviewer": list(getattr(args, "reviewer", []) or []),
        "cc": list(getattr(args, "cc", []) or []),
        "wip": bool(getattr(args, "wip", False)),
        "ready": bool(getattr(args, "ready", False)),
    }


def normalize_requested_paths(paths: Sequence[str]) -> Sequence[str]:
    seen = set()
    normalized = []
    for path in paths:
        if path not in seen:
            normalized.append(path)
            seen.add(path)
    return normalized


def read_message_file(path: str) -> str:
    candidate = Path(path).expanduser()
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError as exc:
        raise CLIUsageError(f"Could not read commit message file {candidate}: {exc}") from exc


def ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


def commit_message_summary(text: str) -> Dict[str, Any]:
    normalized = text.rstrip("\n")
    lines = normalized.splitlines() if normalized else []
    subject = lines[0] if lines else ""
    body_lines = lines[1:] if len(lines) > 1 else []
    return {
        "subject": subject or None,
        "body_line_count": len(body_lines),
        "line_count": len(lines),
        "has_body": bool(body_lines),
    }


def analyze_change_id_text(
    text: str,
    *,
    source: str,
    commit: Optional[str] = None,
    message_file: Optional[str] = None,
) -> Dict[str, Any]:
    values = [match.group("value").strip() for match in CHANGE_ID_TRAILER_RE.finditer(text)]
    invalid_values = [value for value in values if not VALID_CHANGE_ID_RE.fullmatch(value)]
    valid_values = [value for value in values if VALID_CHANGE_ID_RE.fullmatch(value)]
    unique_values = list(dict.fromkeys(values))
    unique_valid_values = list(dict.fromkeys(valid_values))
    primary = valid_values[-1] if valid_values else (values[-1] if values else None)

    if not values:
        status = "missing"
    elif invalid_values:
        status = "invalid"
    elif len(unique_valid_values) > 1:
        status = "multiple"
    else:
        status = "present"

    return {
        "source": source,
        "commit": commit,
        "message_file": message_file,
        "status": status,
        "present": bool(values),
        "valid": bool(valid_values) and not invalid_values and len(unique_valid_values) == 1,
        "multiple": len(unique_values) > 1,
        "value": primary,
        "values": values,
        "valid_values": valid_values,
        "invalid_values": invalid_values,
        "summary": commit_message_summary(text),
    }


def change_id_warnings(document: Mapping[str, Any], *, hook_info: Optional[Mapping[str, Any]] = None) -> Sequence[str]:
    warnings = []
    status = document.get("status")
    if status == "missing":
        warnings.append("Commit message does not contain a Change-Id trailer.")
        if hook_info is not None and not hook_info.get("ok"):
            warnings.append("commit-msg hook is not installed, so Git will not auto-insert a Gerrit Change-Id.")
    elif status == "invalid":
        warnings.append("Commit message contains an invalid Change-Id trailer.")
    elif status == "multiple":
        warnings.append("Commit message contains multiple distinct Change-Id trailers.")
    return warnings


def load_commit_message_from_commit(runner: GitRunner, repo_root: Path, commit: str = "HEAD") -> str:
    result = runner.run(("show", "-s", "--format=%B", commit), cwd=repo_root)
    return result.stdout


def resolve_message_input(
    args: argparse.Namespace,
    runner: GitRunner,
    repo_root: Path,
    *,
    default_commit: Optional[str] = None,
    require_message: bool = False,
) -> Optional[Dict[str, Any]]:
    inline_message = getattr(args, "message", None)
    message_file = getattr(args, "message_file", None)

    if inline_message is not None:
        text = str(inline_message)
        return {
            "source": "inline",
            "text": text,
            "commit": None,
            "message_file": None,
            "summary": commit_message_summary(text),
        }

    if message_file:
        text = read_message_file(message_file)
        return {
            "source": "message_file",
            "text": text,
            "commit": None,
            "message_file": str(Path(message_file).expanduser()),
            "summary": commit_message_summary(text),
        }

    if default_commit is not None:
        text = load_commit_message_from_commit(runner, repo_root, default_commit)
        return {
            "source": "commit",
            "text": text,
            "commit": default_commit,
            "message_file": None,
            "summary": commit_message_summary(text),
        }

    if require_message:
        raise CLIUsageError("Provide --message or --message-file.")

    return None


def load_commit_msg_hook_document(runner: GitRunner, repo_root: Path, git_dir: Path) -> Dict[str, Any]:
    config_map = load_config_map(runner, repo_root)
    return commit_msg_hook_info(config_map, repo_root, git_dir)


def load_status_snapshot_for_paths(runner: GitRunner, repo_root: Path, paths: Sequence[str]) -> Dict[str, Any]:
    if not paths:
        return {
            "entries": [],
            "staged": [],
            "unstaged": [],
            "untracked": [],
            "conflicts": [],
            "ignored": [],
            "is_clean": True,
        }
    result = runner.run(("status", "--porcelain=v1", "--branch", "-z", "--", *paths), cwd=repo_root)
    return parse_status_porcelain(result.stdout)


def diff_name_only(
    runner: GitRunner,
    repo_root: Path,
    *,
    staged: bool,
    paths: Optional[Sequence[str]] = None,
) -> Sequence[str]:
    args = ["diff", "--name-only", "-z"]
    if staged:
        args.append("--cached")
    if paths:
        args.extend(["--", *paths])
    result = runner.run(tuple(args), cwd=repo_root)
    return [item for item in split_nul_output(result.stdout) if item]


def build_commit_plan_document(
    runner: GitRunner,
    repo: Mapping[str, Any],
    args: argparse.Namespace,
    *,
    mode: str,
    require_message: bool,
    default_commit: Optional[str],
) -> tuple[Dict[str, Any], Sequence[str]]:
    repo_root = Path(repo["repo_root"])
    git_dir = Path(repo["git_dir"])
    hook_info = load_commit_msg_hook_document(runner, repo_root, git_dir)
    head_message_text = load_commit_message_from_commit(runner, repo_root, "HEAD") if repo.get("head") else ""
    head_change_id = analyze_change_id_text(head_message_text, source="HEAD", commit="HEAD") if repo.get("head") else None
    proposed_message = resolve_message_input(
        args,
        runner,
        repo_root,
        default_commit=default_commit,
        require_message=require_message,
    )
    proposed_change_id = (
        analyze_change_id_text(
            proposed_message["text"],
            source=proposed_message["source"],
            commit=proposed_message.get("commit"),
            message_file=proposed_message.get("message_file"),
        )
        if proposed_message is not None
        else None
    )
    requested_paths = normalize_requested_paths(list(getattr(args, "paths", []) or []))
    scoped_status = load_status_snapshot_for_paths(runner, repo_root, requested_paths)
    staged_selected = diff_name_only(runner, repo_root, staged=True, paths=requested_paths) if requested_paths else []
    staged_all = diff_name_only(runner, repo_root, staged=True)
    extra_staged_paths = [path for path in staged_all if path not in staged_selected] if requested_paths else []

    warnings = list(repo_warnings(repo))
    if not hook_info["ok"]:
        warnings.append("commit-msg hook is not installed for this repository.")
    if head_change_id is not None:
        warnings.extend(change_id_warnings(head_change_id, hook_info=hook_info))
    if proposed_change_id is not None:
        warnings.extend(change_id_warnings(proposed_change_id, hook_info=hook_info))
    if proposed_message is None and mode == "create":
        warnings.append("No proposed commit message was provided for commit planning.")

    document = {
        "repo_root": repo["repo_root"],
        "mode": mode,
        "requested_paths": requested_paths,
        "head": {
            "commit": repo.get("head"),
            "short_commit": repo.get("head_short"),
            "change_id": head_change_id,
            "message": commit_message_summary(head_message_text) if repo.get("head") else None,
        },
        "message": {
            "source": proposed_message.get("source") if proposed_message is not None else None,
            "commit": proposed_message.get("commit") if proposed_message is not None else None,
            "message_file": proposed_message.get("message_file") if proposed_message is not None else None,
            "summary": proposed_message.get("summary") if proposed_message is not None else None,
            "change_id": proposed_change_id,
        },
        "hooks": {
            "commit_msg": hook_info,
        },
        "status": {
            "is_clean": repo["status"]["is_clean"],
            "staged": repo["status"]["staged"],
            "unstaged": repo["status"]["unstaged"],
            "untracked": repo["status"]["untracked"],
            "conflicts": repo["status"]["conflicts"],
        },
        "path_scope": {
            "entries": scoped_status.get("entries", []),
            "staged": scoped_status.get("staged", []),
            "unstaged": scoped_status.get("unstaged", []),
            "untracked": scoped_status.get("untracked", []),
            "conflicts": scoped_status.get("conflicts", []),
            "is_clean": scoped_status.get("is_clean", True),
            "staged_paths": staged_selected,
            "extra_staged_paths": extra_staged_paths,
        },
    }
    return document, warnings


def stage_explicit_paths(runner: GitRunner, repo_root: Path, paths: Sequence[str]) -> None:
    if not paths:
        return
    runner.run(("add", "--", *paths), cwd=repo_root)


def write_temp_commit_message(message_text: str) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="active-gerrit-commit-",
        suffix=".txt",
        delete=False,
    )
    try:
        temp_file.write(ensure_trailing_newline(message_text))
        temp_file.flush()
    finally:
        temp_file.close()
    return Path(temp_file.name)


def validate_commit_create_change_id(
    proposed_change_id: Optional[Mapping[str, Any]],
    hook_info: Mapping[str, Any],
    *,
    allow_missing_change_id: bool,
) -> None:
    if proposed_change_id is None:
        return
    status = proposed_change_id.get("status")
    if status == "invalid":
        raise CLIUsageError("Commit message contains an invalid Change-Id trailer.")
    if status == "multiple":
        raise CLIUsageError("Commit message contains multiple distinct Change-Id trailers.")
    if status == "missing" and not hook_info.get("ok") and not allow_missing_change_id:
        raise CLIUsageError(
            "Commit message does not contain a Change-Id trailer and commit-msg hook is not installed. "
            "Provide a Change-Id or pass --allow-missing-change-id explicitly."
        )


def validate_commit_amend_change_id(
    head_change_id: Optional[Mapping[str, Any]],
    proposed_change_id: Optional[Mapping[str, Any]],
    *,
    allow_change_id_change: bool,
) -> None:
    if allow_change_id_change or head_change_id is None or proposed_change_id is None:
        return
    old_value = head_change_id.get("value")
    new_value = proposed_change_id.get("value")
    if old_value and old_value != new_value:
        raise CLIUsageError(
            f"commit-amend would change Change-Id from {old_value} to {new_value or '<missing>'}. "
            "Pass --allow-change-id-change only when you intentionally want a new review."
        )


def execute_commit(
    runner: GitRunner,
    repo_root: Path,
    *,
    amend: bool,
    message_text: str,
    paths: Sequence[str],
) -> Dict[str, Any]:
    message_file = write_temp_commit_message(message_text)
    try:
        args = ["commit"]
        if amend:
            args.append("--amend")
        args.extend(["--file", str(message_file)])
        if paths:
            args.append("--only")
            args.extend(["--", *paths])
        result = runner.run(tuple(args), cwd=repo_root)
    finally:
        try:
            message_file.unlink()
        except OSError:
            pass

    head_result = runner.run(("rev-parse", "--verify", "HEAD"), cwd=repo_root)
    short_head_result = runner.run(("rev-parse", "--short", "HEAD"), cwd=repo_root)
    subject_result = runner.run(("show", "-s", "--format=%s", "HEAD"), cwd=repo_root)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "head": head_result.stdout.strip(),
        "head_short": short_head_result.stdout.strip(),
        "subject": subject_result.stdout.strip() or None,
    }


def build_push_review_plan_document(
    runner: GitRunner,
    repo: Mapping[str, Any],
    args: argparse.Namespace,
    env: Mapping[str, str],
) -> tuple[Dict[str, Any], Sequence[str]]:
    if not repo.get("head"):
        raise CLIUsageError("push-review requires an existing HEAD commit.")

    repo_root = Path(repo["repo_root"])
    git_dir = Path(repo["git_dir"])
    config_map = load_config_map(runner, repo_root)
    hook_info = commit_msg_hook_info(config_map, repo_root, git_dir)
    branch_info = resolve_review_branch(args, env, repo, config_map)
    remote_info = select_gerrit_remote(
        repo.get("remotes") or [],
        explicit_remote=getattr(args, "remote", None),
        env=env,
    )
    remote_name = remote_info.get("name")
    if not isinstance(remote_name, str) or not remote_name:
        raise GitCommandError("Could not determine a Gerrit remote for review push.")

    head_message_text = load_commit_message_from_commit(runner, repo_root, "HEAD")
    change_id = analyze_change_id_text(head_message_text, source="HEAD", commit="HEAD")
    options = review_ref_options_from_args(args)
    try:
        target_ref = build_review_ref(branch_info["branch"], options)
    except ValueError as exc:
        raise CLIUsageError(str(exc)) from exc

    remote_branch_ref = f"refs/heads/{branch_info['branch']}"
    remote_branch_probe = runner.run(("ls-remote", "--heads", remote_name, remote_branch_ref), cwd=repo_root, check=False)
    remote_branch_exists = bool(remote_branch_probe.stdout.strip())
    warnings = [*repo_warnings(repo), *list(remote_info.get("warnings") or []), *change_id_warnings(change_id, hook_info=hook_info)]
    if not hook_info["ok"]:
        warnings.append("commit-msg hook is not installed for this repository.")
    if not remote_branch_exists:
        warnings.append(f"Remote {remote_name} does not currently advertise {remote_branch_ref}.")

    document = {
        "repo_root": repo["repo_root"],
        "remote": remote_name,
        "remote_reason": remote_info.get("reason"),
        "branch": branch_info["branch"],
        "branch_source": branch_info["source"],
        "current_branch": repo.get("current_branch"),
        "upstream": repo.get("upstream"),
        "head": repo.get("head"),
        "head_short": repo.get("head_short"),
        "subject": change_id["summary"]["subject"],
        "change_id": change_id,
        "options": options,
        "target_ref": target_ref,
        "refspec": f"HEAD:{target_ref}",
        "requires_clean_worktree": True,
        "hooks": {
            "commit_msg": hook_info,
        },
        "remote_branch": {
            "ref": remote_branch_ref,
            "exists": remote_branch_exists,
        },
    }
    return document, warnings


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if status:
            raise CLIUsageError((message or "").strip() or f"argparse exited with status {status}")
        raise SystemExit(status)


def build_runner(args: argparse.Namespace, env: Mapping[str, str]) -> GitRunner:
    return GitRunner(GitRunnerConfig.from_args_env(args, env), env)


def runner_config_data(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    config = GitRunnerConfig.from_args_env(args, env)
    return {
        "git_bin": config.git_bin,
        "repo": str(config.repo) if config.repo else None,
        "timeout_seconds": config.timeout_seconds,
        "output_limit_chars": config.output_limit_chars,
    }


def handle_ping(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    warnings = []
    if args.dry_run:
        warnings.append("--dry-run is accepted globally and will be enforced by write-capable commands.")
    if args.yes:
        warnings.append("--yes is accepted globally but ping does not execute high-risk actions.")
    if args.trace:
        warnings.append("--trace is accepted for future audit metadata.")

    data = {
        "ready": True,
        "cli": CLI_NAME,
        "runner": runner_config_data(args, env),
        "reserved_options": {
            "repo": args.repo,
            "timeout": args.timeout,
            "trace": args.trace,
            "dry_run": args.dry_run,
            "yes": args.yes,
        },
        "planned_commands": [name for name, _help in PLANNED_COMMANDS],
    }
    return success_envelope("ping", data, args, env, warnings=warnings)


def split_nul_output(text: str) -> Sequence[str]:
    if not text:
        return []
    parts = text.split("\0")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def parse_config_list(text: str) -> Dict[str, Sequence[str]]:
    data: Dict[str, list[str]] = {}
    for item in split_nul_output(text):
        if "\n" in item:
            key, value = item.split("\n", 1)
        else:
            key, value = item, ""
        data.setdefault(key, []).append(value)
    return data


def config_value(config_map: Mapping[str, Sequence[str]], key: str) -> Optional[str]:
    values = config_map.get(key)
    if not values:
        return None
    return values[-1]


def resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if path.is_absolute():
        return path
    return (base / path).resolve()


def git_binary_details(config: GitRunnerConfig) -> Dict[str, Any]:
    path: Optional[str]
    git_bin = config.git_bin
    if os.path.sep in git_bin or (os.path.altsep and os.path.altsep in git_bin):
        candidate = Path(git_bin).expanduser()
        path = str(candidate.resolve()) if candidate.exists() else None
    else:
        path = shutil.which(git_bin)
    details: Dict[str, Any] = {
        "ok": path is not None,
        "required": True,
        "path": path,
        "configured_git_bin": git_bin,
    }
    return details


def parse_branch_header(header: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "raw": header,
        "branch": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "detached": False,
        "unborn": False,
        "upstream_gone": False,
    }
    if not header.startswith("## "):
        return info

    body = header[3:]
    if body.startswith("No commits yet on "):
        info["branch"] = body[len("No commits yet on ") :]
        info["unborn"] = True
        return info

    body_part = body
    state_text = None
    if " [" in body and body.endswith("]"):
        body_part, state_text = body.rsplit(" [", 1)
        state_text = state_text[:-1]

    if "..." in body_part:
        branch, upstream = body_part.split("...", 1)
        info["branch"] = branch
        info["upstream"] = upstream or None
    else:
        info["branch"] = body_part

    branch_name = info["branch"]
    if isinstance(branch_name, str) and branch_name.startswith("HEAD"):
        info["detached"] = True
        info["branch"] = None

    if state_text:
        for item in state_text.split(", "):
            if item == "gone":
                info["upstream_gone"] = True
                continue
            if item.startswith("ahead "):
                try:
                    info["ahead"] = int(item[len("ahead ") :])
                except ValueError:
                    pass
                continue
            if item.startswith("behind "):
                try:
                    info["behind"] = int(item[len("behind ") :])
                except ValueError:
                    pass
    return info


def normalize_status_entry(code: str, path: str, old_path: Optional[str] = None) -> Dict[str, Any]:
    index_status = code[0]
    worktree_status = code[1]
    return {
        "code": code,
        "index_status": index_status,
        "worktree_status": worktree_status,
        "path": path,
        "old_path": old_path,
        "conflict": code in CONFLICT_CODES or "U" in code,
    }


def parse_status_porcelain(text: str) -> Dict[str, Any]:
    parts = list(split_nul_output(text))
    header = parts[0] if parts and parts[0].startswith("## ") else ""
    index = 1 if header else 0
    entries = []
    staged = []
    unstaged = []
    untracked = []
    conflicts = []
    ignored = []

    while index < len(parts):
        record = parts[index]
        if len(record) < 3:
            index += 1
            continue
        code = record[:2]
        path = record[3:]
        old_path = None
        if code[0] in {"R", "C"} or code[1] in {"R", "C"}:
            if index + 1 < len(parts):
                old_path = parts[index + 1]
                index += 1
        entry = normalize_status_entry(code, path, old_path=old_path)
        entries.append(entry)

        if code == "??":
            untracked.append(entry)
        elif code == "!!":
            ignored.append(entry)
        else:
            if entry["conflict"]:
                conflicts.append(entry)
            if entry["index_status"] not in {" ", "?", "!"}:
                staged.append(entry)
            if entry["worktree_status"] not in {" ", "?", "!"}:
                unstaged.append(entry)
        index += 1

    branch_info = parse_branch_header(header)
    return {
        "branch_header": header,
        "branch": branch_info["branch"],
        "upstream": branch_info["upstream"],
        "ahead": branch_info["ahead"],
        "behind": branch_info["behind"],
        "detached": branch_info["detached"],
        "unborn": branch_info["unborn"],
        "upstream_gone": branch_info["upstream_gone"],
        "entries": entries,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "conflicts": conflicts,
        "ignored": ignored,
        "is_clean": not staged and not unstaged and not untracked and not conflicts,
    }


def config_entries_document(config_map: Mapping[str, Sequence[str]], keys: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    document: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        value = config_value(config_map, key)
        document[key] = {
            "ok": value is not None and value != "",
            "value": value,
        }
    return document


def parse_diff_name_status(text: str) -> Sequence[Dict[str, Any]]:
    parts = list(split_nul_output(text))
    entries = []
    index = 0
    while index < len(parts):
        raw_status = parts[index]
        index += 1
        if not raw_status:
            continue
        if index >= len(parts):
            break
        first_path = parts[index]
        index += 1
        path = first_path
        old_path = None
        similarity = None
        status = raw_status[0]
        if status in {"R", "C"}:
            old_path = first_path
            if index >= len(parts):
                break
            path = parts[index]
            index += 1
            similarity_text = raw_status[1:]
            similarity = int(similarity_text) if similarity_text.isdigit() else None
        entries.append(
            {
                "path": path,
                "old_path": old_path,
                "status": status,
                "status_label": DIFF_STATUS_LABELS.get(status, "unknown"),
                "raw_status": raw_status,
                "similarity": similarity,
            }
        )
    return entries


def parse_numstat_value(value: str) -> Optional[int]:
    if value == "-":
        return None
    return int(value)


def parse_diff_numstat(text: str) -> Sequence[Dict[str, Any]]:
    parts = list(split_nul_output(text))
    entries = []
    index = 0
    while index < len(parts):
        record = parts[index]
        index += 1
        if not record:
            continue
        fields = record.split("\t", 2)
        if len(fields) < 3:
            continue
        insertions_text, deletions_text, path = fields
        old_path = None
        if path == "":
            if index + 1 >= len(parts):
                break
            old_path = parts[index]
            path = parts[index + 1]
            index += 2
        insertions = parse_numstat_value(insertions_text)
        deletions = parse_numstat_value(deletions_text)
        entries.append(
            {
                "path": path,
                "old_path": old_path,
                "insertions": insertions,
                "deletions": deletions,
                "binary": insertions is None or deletions is None,
            }
        )
    return entries


def merge_diff_entries(name_entries: Sequence[Mapping[str, Any]], stat_entries: Sequence[Mapping[str, Any]]) -> Sequence[Dict[str, Any]]:
    merged = []
    for index, name_entry in enumerate(name_entries):
        stat_entry = stat_entries[index] if index < len(stat_entries) else {}
        merged.append(
            {
                "path": name_entry.get("path"),
                "old_path": name_entry.get("old_path"),
                "status": name_entry.get("status"),
                "status_label": name_entry.get("status_label"),
                "raw_status": name_entry.get("raw_status"),
                "similarity": name_entry.get("similarity"),
                "insertions": stat_entry.get("insertions"),
                "deletions": stat_entry.get("deletions"),
                "binary": bool(stat_entry.get("binary", False)),
            }
        )
    return merged


def build_diff_stat(entries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    insertions = 0
    deletions = 0
    binary_files = 0
    renamed = 0
    copied = 0
    deleted = 0
    for entry in entries:
        if entry.get("binary"):
            binary_files += 1
        if isinstance(entry.get("insertions"), int):
            insertions += int(entry["insertions"])
        if isinstance(entry.get("deletions"), int):
            deletions += int(entry["deletions"])
        if entry.get("status") == "R":
            renamed += 1
        if entry.get("status") == "C":
            copied += 1
        if entry.get("status") == "D":
            deleted += 1
    return {
        "files_changed": len(entries),
        "insertions": insertions,
        "deletions": deletions,
        "binary_files": binary_files,
        "renamed_files": renamed,
        "copied_files": copied,
        "deleted_files": deleted,
    }


def diff_context(staged: bool, base: Optional[str]) -> Dict[str, Any]:
    if staged:
        return {
            "base": base or "HEAD",
            "target": "index",
            "staged": True,
        }
    return {
        "base": base or "index",
        "target": "working-tree",
        "staged": False,
    }


def build_diff_args(
    *,
    staged: bool,
    base: Optional[str],
    name_status: bool = False,
    numstat: bool = False,
    path: Optional[str] = None,
) -> list[str]:
    args = ["diff", "--find-renames", "--find-copies-harder"]
    if staged:
        args.append("--cached")
    if name_status:
        args.extend(["--name-status", "-z"])
    if numstat:
        args.extend(["--numstat", "-z"])
    if base:
        args.append(base)
    if path is not None:
        args.extend(["--", path])
    return args


def load_diff_document(
    runner: GitRunner,
    repo_root: Path,
    *,
    staged: bool,
    base: Optional[str],
    path: Optional[str] = None,
    include_patch: bool = False,
) -> Dict[str, Any]:
    name_result = runner.run(tuple(build_diff_args(staged=staged, base=base, name_status=True, path=path)), cwd=repo_root)
    numstat_result = runner.run(tuple(build_diff_args(staged=staged, base=base, numstat=True, path=path)), cwd=repo_root)
    name_entries = parse_diff_name_status(name_result.stdout)
    stat_entries = parse_diff_numstat(numstat_result.stdout)
    files = merge_diff_entries(name_entries, stat_entries)
    document = {
        **diff_context(staged, base),
        "files": files,
        "stat": build_diff_stat(files),
        "patch": None,
        "patch_truncated": False,
    }
    if include_patch:
        patch_result = runner.run(tuple(build_diff_args(staged=staged, base=base, path=path)), cwd=repo_root)
        document["patch"] = patch_result.stdout
        document["patch_truncated"] = patch_result.stdout_truncated
    return document


def parse_structured_records(text: str, field_names: Sequence[str]) -> Sequence[Dict[str, str]]:
    records = []
    for raw_record in text.split(RECORD_SEPARATOR):
        if not raw_record:
            continue
        record = raw_record.rstrip("\n")
        parts = record.split(FIELD_SEPARATOR, len(field_names) - 1)
        while len(parts) < len(field_names):
            parts.append("")
        records.append(dict(zip(field_names, parts)))
    return records


def parse_parent_list(value: str) -> Sequence[str]:
    return [parent for parent in value.split() if parent]


def normalize_commit_record(record: Mapping[str, str]) -> Dict[str, Any]:
    body = record.get("body", "")
    return {
        "commit": record.get("commit") or None,
        "short_commit": record.get("short_commit") or None,
        "author_name": record.get("author_name") or None,
        "author_email": record.get("author_email") or None,
        "date": record.get("date") or None,
        "subject": record.get("subject") or None,
        "parents": parse_parent_list(record.get("parents", "")),
        "body": body.rstrip("\n"),
    }


def load_log_commits(
    runner: GitRunner,
    repo_root: Path,
    *,
    limit: int,
    revision_range: Optional[str],
    path: Optional[str],
) -> Sequence[Dict[str, Any]]:
    format_string = (
        f"{RECORD_SEPARATOR}%H{FIELD_SEPARATOR}%h{FIELD_SEPARATOR}%an{FIELD_SEPARATOR}%ae"
        f"{FIELD_SEPARATOR}%ad{FIELD_SEPARATOR}%s{FIELD_SEPARATOR}%P"
    )
    args = ["log", f"-n{limit}", "--date=iso-strict", f"--format={format_string}"]
    if revision_range:
        args.append(revision_range)
    if path is not None:
        args.extend(["--", path])
    result = runner.run(tuple(args), cwd=repo_root)
    records = parse_structured_records(
        result.stdout,
        ("commit", "short_commit", "author_name", "author_email", "date", "subject", "parents"),
    )
    commits = []
    for record in records:
        normalized = normalize_commit_record(record)
        normalized.pop("body", None)
        commits.append(normalized)
    return commits


def load_show_commit(
    runner: GitRunner,
    repo_root: Path,
    *,
    commit: str,
    include_patch: bool,
) -> Dict[str, Any]:
    format_string = (
        f"{RECORD_SEPARATOR}%H{FIELD_SEPARATOR}%h{FIELD_SEPARATOR}%an{FIELD_SEPARATOR}%ae"
        f"{FIELD_SEPARATOR}%ad{FIELD_SEPARATOR}%s{FIELD_SEPARATOR}%P{FIELD_SEPARATOR}%B"
    )
    meta_result = runner.run(("show", "--quiet", "--date=iso-strict", f"--format={format_string}", commit), cwd=repo_root)
    records = parse_structured_records(
        meta_result.stdout,
        ("commit", "short_commit", "author_name", "author_email", "date", "subject", "parents", "body"),
    )
    if not records:
        raise GitCommandError(f"Could not load commit metadata for {commit}.")
    document = normalize_commit_record(records[0])
    diff_document = load_show_diff_document(runner, repo_root, commit=commit, include_patch=include_patch)
    document.update(diff_document)
    return document


def load_show_diff_document(
    runner: GitRunner,
    repo_root: Path,
    *,
    commit: str,
    include_patch: bool,
) -> Dict[str, Any]:
    name_args = ("diff-tree", "--root", "--no-commit-id", "--find-renames", "--find-copies-harder", "--name-status", "-z", "-r", commit)
    numstat_args = ("diff-tree", "--root", "--no-commit-id", "--numstat", "-z", "-r", commit)
    name_result = runner.run(name_args, cwd=repo_root)
    numstat_result = runner.run(numstat_args, cwd=repo_root)
    files = merge_diff_entries(parse_diff_name_status(name_result.stdout), parse_diff_numstat(numstat_result.stdout))
    patch = None
    patch_truncated = False
    if include_patch:
        patch_result = runner.run(("show", "--format=", "--find-renames", "--find-copies-harder", commit), cwd=repo_root)
        patch = patch_result.stdout
        patch_truncated = patch_result.stdout_truncated
    return {
        "files": files,
        "stat": build_diff_stat(files),
        "patch": patch,
        "patch_truncated": patch_truncated,
    }


def load_branches(
    runner: GitRunner,
    repo_root: Path,
    *,
    include_all: bool,
    remotes_only: bool,
    contains: Optional[str],
    sort_key: Optional[str],
) -> Sequence[Dict[str, Any]]:
    format_string = (
        f"{RECORD_SEPARATOR}%(HEAD){FIELD_SEPARATOR}%(refname:short){FIELD_SEPARATOR}%(refname)"
        f"{FIELD_SEPARATOR}%(objectname){FIELD_SEPARATOR}%(upstream:short)"
        f"{FIELD_SEPARATOR}%(upstream:trackshort){FIELD_SEPARATOR}%(subject)"
    )
    args = ["branch", f"--format={format_string}"]
    if include_all:
        args.append("--all")
    elif remotes_only:
        args.append("--remotes")
    if contains:
        args.extend(["--contains", contains])
    if sort_key:
        args.extend(["--sort", sort_key])
    result = runner.run(tuple(args), cwd=repo_root)
    records = parse_structured_records(
        result.stdout,
        ("head", "name", "full_name", "objectname", "upstream", "upstream_trackshort", "subject"),
    )
    branches = []
    for record in records:
        full_name = record.get("full_name", "")
        branches.append(
            {
                "name": record.get("name") or None,
                "full_name": full_name or None,
                "current": (record.get("head") or "").strip() == "*",
                "remote": full_name.startswith("refs/remotes/"),
                "objectname": record.get("objectname") or None,
                "upstream": record.get("upstream") or None,
                "upstream_trackshort": record.get("upstream_trackshort") or None,
                "subject": record.get("subject") or None,
            }
        )
    return branches


def load_status_snapshot(runner: GitRunner, repo_root: Path) -> Dict[str, Any]:
    result = runner.run(("status", "--porcelain=v1", "--branch", "-z"), cwd=repo_root)
    status = parse_status_porcelain(result.stdout)
    stash = runner.run(("stash", "list", "--format=%H"), cwd=repo_root)
    stash_lines = [line for line in stash.stdout.splitlines() if line.strip()]
    status["stash_count"] = len(stash_lines)
    return status


def load_config_map(runner: GitRunner, cwd: Optional[Path]) -> Dict[str, Sequence[str]]:
    result = runner.run(("config", "--null", "--list"), cwd=cwd)
    return parse_config_list(result.stdout)


def load_remotes(runner: GitRunner, repo_root: Path) -> Sequence[Dict[str, Any]]:
    names_result = runner.run(("remote",), cwd=repo_root)
    remotes = []
    for name in [line.strip() for line in names_result.stdout.splitlines() if line.strip()]:
        fetch_result = runner.run(("remote", "get-url", "--all", name), cwd=repo_root, check=False)
        push_result = runner.run(("remote", "get-url", "--push", "--all", name), cwd=repo_root, check=False)
        fetch_urls = [line.strip() for line in fetch_result.stdout.splitlines() if line.strip()]
        push_urls = [line.strip() for line in push_result.stdout.splitlines() if line.strip()]
        remotes.append(
            {
                "name": name,
                "fetch_urls": fetch_urls,
                "push_urls": push_urls,
                "fetch_url": fetch_urls[0] if fetch_urls else None,
                "push_url": push_urls[0] if push_urls else None,
            }
        )
    return remotes


def load_head_info(runner: GitRunner, repo_root: Path) -> Dict[str, Optional[str]]:
    head_result = runner.run(("rev-parse", "--verify", "HEAD"), cwd=repo_root, check=False)
    if head_result.returncode != 0:
        return {"head": None, "head_short": None}
    full_head = head_result.stdout.strip() or None
    short_result = runner.run(("rev-parse", "--short", "HEAD"), cwd=repo_root, check=False)
    short_head = short_result.stdout.strip() or None
    return {"head": full_head, "head_short": short_head}


def inspect_repo(runner: GitRunner) -> Dict[str, Any]:
    repo_root = runner.resolve_repo_root()
    git_dir_result = runner.run(("rev-parse", "--git-dir"), cwd=repo_root)
    inside_result = runner.run(("rev-parse", "--is-inside-work-tree"), cwd=repo_root)
    status = load_status_snapshot(runner, repo_root)
    remotes = load_remotes(runner, repo_root)
    head_info = load_head_info(runner, repo_root)
    git_dir_raw = git_dir_result.stdout.strip()
    git_dir = resolve_path(repo_root, git_dir_raw)
    return {
        "repo_root": str(repo_root),
        "git_dir": str(git_dir),
        "git_dir_raw": git_dir_raw,
        "is_inside_work_tree": inside_result.stdout.strip().lower() == "true",
        "current_branch": status["branch"],
        "upstream": status["upstream"],
        "ahead": status["ahead"],
        "behind": status["behind"],
        "detached": status["detached"],
        "unborn": status["unborn"],
        "upstream_gone": status["upstream_gone"],
        "head": head_info["head"],
        "head_short": head_info["head_short"],
        "stash_count": status["stash_count"],
        "remotes": remotes,
        "status": status,
    }


def commit_msg_hook_info(config_map: Mapping[str, Sequence[str]], repo_root: Path, git_dir: Path) -> Dict[str, Any]:
    hooks_path = config_value(config_map, "core.hooksPath")
    hooks_dir = resolve_path(repo_root, hooks_path) if hooks_path else (git_dir / "hooks")
    commit_msg = hooks_dir / "commit-msg"
    exists = commit_msg.exists()
    return {
        "ok": exists,
        "required": False,
        "path": str(commit_msg),
        "configured_hooks_path": str(hooks_dir),
        "executable": os.access(commit_msg, os.X_OK) if exists else False,
        "hint": None if exists else "Install the Gerrit commit-msg hook if this repository should upload reviews with stable Change-Id trailers.",
    }


def repo_warnings(repo: Mapping[str, Any]) -> Sequence[str]:
    warnings = []
    if not repo.get("upstream"):
        warnings.append("No upstream branch is configured for the current branch.")
    if repo.get("upstream_gone"):
        warnings.append("The configured upstream branch is gone.")
    if repo.get("detached"):
        warnings.append("HEAD is detached; review push workflows should confirm the target branch explicitly.")
    if repo.get("unborn"):
        warnings.append("The current branch has no commits yet.")
    return warnings


def repo_summary_document(repo: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "repo_root": repo["repo_root"],
        "git_dir": repo["git_dir"],
        "is_inside_work_tree": repo["is_inside_work_tree"],
        "current_branch": repo["current_branch"],
        "upstream": repo["upstream"],
        "ahead": repo["ahead"],
        "behind": repo["behind"],
        "detached": repo["detached"],
        "unborn": repo["unborn"],
        "upstream_gone": repo["upstream_gone"],
        "head": repo["head"],
        "head_short": repo["head_short"],
        "stash_count": repo["stash_count"],
        "remotes": repo["remotes"],
    }


def handle_repo_info(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    return success_envelope("repo-info", repo_summary_document(repo), args, env, warnings=repo_warnings(repo))


def handle_repo_status(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    status = repo["status"]
    document = {
        "repo_root": repo["repo_root"],
        "git_dir": repo["git_dir"],
        "branch": status["branch"],
        "upstream": status["upstream"],
        "ahead": status["ahead"],
        "behind": status["behind"],
        "detached": status["detached"],
        "unborn": status["unborn"],
        "upstream_gone": status["upstream_gone"],
        "is_clean": status["is_clean"],
        "staged": status["staged"],
        "unstaged": status["unstaged"],
        "untracked": status["untracked"],
        "conflicts": status["conflicts"],
        "ignored": status["ignored"],
        "entries": status["entries"],
        "stash_count": status["stash_count"],
    }
    return success_envelope("repo-status", document, args, env, warnings=repo_warnings(repo))


def handle_repo_remotes(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    document = {
        "repo_root": repo["repo_root"],
        "current_branch": repo["current_branch"],
        "upstream": repo["upstream"],
        "remotes": repo["remotes"],
    }
    warnings = list(repo_warnings(repo))
    if not repo["remotes"]:
        warnings.append("No Git remotes are configured for this repository.")
    return success_envelope("repo-remotes", document, args, env, warnings=warnings)


def handle_repo_config(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    config_map = load_config_map(runner, Path(repo["repo_root"]))
    branch = repo["current_branch"]
    keys = list(CONFIG_KEYS)
    if branch:
        keys.extend((f"branch.{branch}.remote", f"branch.{branch}.merge"))
    config_document = config_entries_document(config_map, keys)
    hook_info = commit_msg_hook_info(config_map, Path(repo["repo_root"]), Path(repo["git_dir"]))
    document = {
        "repo_root": repo["repo_root"],
        "current_branch": branch,
        "config": config_document,
        "hooks": {
            "commit_msg": hook_info,
        },
    }
    warnings = list(repo_warnings(repo))
    if not config_document["user.name"]["ok"]:
        warnings.append("Git user.name is not configured.")
    if not config_document["user.email"]["ok"]:
        warnings.append("Git user.email is not configured.")
    if not hook_info["ok"]:
        warnings.append("commit-msg hook is not installed for this repository.")
    return success_envelope("repo-config", document, args, env, warnings=warnings)


def handle_repo_diff(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    diff = load_diff_document(
        runner,
        Path(repo["repo_root"]),
        staged=args.staged,
        base=args.base,
        include_patch=args.include_patch and not args.stat_only,
    )
    document = {
        "repo_root": repo["repo_root"],
        **diff,
        "requested_base": args.base,
        "stat_only": args.stat_only,
        "include_patch": bool(args.include_patch and not args.stat_only),
    }
    if args.stat_only:
        document["patch"] = None
        document["patch_truncated"] = False
    return success_envelope("repo-diff", document, args, env, warnings=repo_warnings(repo))


def handle_repo_diff_file(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    include_patch = not args.stat_only
    diff = load_diff_document(
        runner,
        Path(repo["repo_root"]),
        staged=args.staged,
        base=args.base,
        path=args.path,
        include_patch=include_patch,
    )
    document = {
        "repo_root": repo["repo_root"],
        "path": args.path,
        **diff,
        "requested_base": args.base,
        "stat_only": args.stat_only,
        "include_patch": include_patch,
    }
    return success_envelope("repo-diff-file", document, args, env, warnings=repo_warnings(repo))


def handle_repo_log(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    commits = load_log_commits(
        runner,
        Path(repo["repo_root"]),
        limit=args.limit,
        revision_range=args.revision_range,
        path=args.path,
    )
    document = {
        "repo_root": repo["repo_root"],
        "limit": args.limit,
        "revision_range": args.revision_range,
        "path": args.path,
        "commits": commits,
    }
    return success_envelope("repo-log", document, args, env, warnings=repo_warnings(repo))


def handle_repo_show(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    commit = load_show_commit(
        runner,
        Path(repo["repo_root"]),
        commit=args.commit,
        include_patch=args.include_patch and not args.stat_only,
    )
    document = {
        "repo_root": repo["repo_root"],
        "requested_commit": args.commit,
        **commit,
        "include_patch": bool(args.include_patch and not args.stat_only),
        "stat_only": args.stat_only,
    }
    if args.stat_only:
        document["patch"] = None
        document["patch_truncated"] = False
    return success_envelope("repo-show", document, args, env, warnings=repo_warnings(repo))


def handle_repo_branches(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    branches = load_branches(
        runner,
        Path(repo["repo_root"]),
        include_all=args.all,
        remotes_only=args.remotes,
        contains=args.contains,
        sort_key=args.sort,
    )
    document = {
        "repo_root": repo["repo_root"],
        "current_branch": repo["current_branch"],
        "contains": args.contains,
        "sort": args.sort,
        "all": args.all,
        "remotes_only": args.remotes,
        "branches": branches,
    }
    return success_envelope("repo-branches", document, args, env, warnings=repo_warnings(repo))


def handle_fetch_change(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    fetched = fetch_change_revision(runner, repo, args, env)
    document = {
        "repo_root": repo["repo_root"],
        **fetched,
    }
    warnings = list(repo_warnings(repo)) + list(fetched.get("remote_warnings") or [])
    return success_envelope("fetch-change", document, args, env, warnings=warnings)


def handle_checkout_change(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    if not args.allow_dirty and str(env.get("GIT_ALLOW_DIRTY_CHECKOUT", "false")).lower() not in {"1", "true", "yes", "on"}:
        enforce_clean_worktree_or_fail(repo)

    fetched = fetch_change_revision(runner, repo, args, env)
    repo_root = Path(repo["repo_root"])

    checkout_mode = "detached" if args.detach else "branch"
    branch_name = None
    if args.detach:
        runner.run(("switch", "--detach", "FETCH_HEAD"), cwd=repo_root)
    else:
        branch_name = args.branch or default_review_branch(fetched, args.change, fetched)
        ensure_branch_absent(runner, repo_root, branch_name)
        runner.run(("switch", "-c", branch_name, "FETCH_HEAD"), cwd=repo_root)

    current_branch_result = runner.run(("branch", "--show-current"), cwd=repo_root, check=False)
    document = {
        "repo_root": repo["repo_root"],
        **fetched,
        "checkout_mode": checkout_mode,
        "branch": branch_name,
        "current_branch": current_branch_result.stdout.strip() or None,
        "worktree": None,
    }
    warnings = list(repo_warnings(repo)) + list(fetched.get("remote_warnings") or [])
    return success_envelope("checkout-change", document, args, env, warnings=warnings)


def handle_worktree_change(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    fetched = fetch_change_revision(runner, repo, args, env)
    repo_root = Path(repo["repo_root"])

    checkout_mode = "detached" if args.detach else "branch"
    branch_name = None if args.detach else (args.branch or default_review_branch(fetched, args.change, fetched))
    if branch_name is not None:
        ensure_branch_absent(runner, repo_root, branch_name)

    worktree_path = Path(args.path).expanduser() if args.path else default_worktree_path(repo_root, branch_name or f"change-{args.change}")
    if worktree_path.exists() and any(worktree_path.iterdir()):
        raise GitCommandError(f"Worktree target path is not empty: {worktree_path}")

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["worktree", "add"]
    if args.detach:
        command.append("--detach")
    if branch_name is not None:
        command.extend(["-b", branch_name])
    command.extend([str(worktree_path), "FETCH_HEAD"])
    runner.run(tuple(command), cwd=repo_root)

    worktree_repo_root = runner.run(("rev-parse", "--show-toplevel"), cwd=worktree_path)
    worktree_head = runner.run(("rev-parse", "--verify", "HEAD"), cwd=worktree_path)
    current_branch_result = runner.run(("branch", "--show-current"), cwd=worktree_path, check=False)
    document = {
        "repo_root": repo["repo_root"],
        **fetched,
        "checkout_mode": checkout_mode,
        "branch": branch_name,
        "current_branch": current_branch_result.stdout.strip() or None,
        "worktree": {
            "path": str(worktree_path),
            "repo_root": worktree_repo_root.stdout.strip(),
            "head": worktree_head.stdout.strip(),
        },
    }
    warnings = list(repo_warnings(repo)) + list(fetched.get("remote_warnings") or [])
    return success_envelope("worktree-change", document, args, env, warnings=warnings)


def handle_change_id_check(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    repo_root = Path(repo["repo_root"])
    hook_info = load_commit_msg_hook_document(runner, repo_root, Path(repo["git_dir"]))

    if args.message_file:
        message_path = str(Path(args.message_file).expanduser())
        message_text = read_message_file(message_path)
        document = analyze_change_id_text(message_text, source="message_file", message_file=message_path)
    else:
        commit_ref = args.commit or "HEAD"
        message_text = load_commit_message_from_commit(runner, repo_root, commit_ref)
        document = analyze_change_id_text(message_text, source="commit", commit=commit_ref)

    payload = {
        "repo_root": repo["repo_root"],
        **document,
        "hooks": {
            "commit_msg": hook_info,
        },
    }
    warnings = list(dict.fromkeys([*repo_warnings(repo), *change_id_warnings(document, hook_info=hook_info)]))
    return success_envelope("change-id-check", payload, args, env, warnings=warnings)


def handle_commit_plan(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    mode = "amend" if args.amend else "create"
    document, warnings = build_commit_plan_document(
        runner,
        repo,
        args,
        mode=mode,
        require_message=False,
        default_commit="HEAD" if args.amend and repo.get("head") else None,
    )
    if document["requested_paths"] and document["path_scope"]["extra_staged_paths"]:
        warnings = [
            *warnings,
            "There are staged changes outside the requested path scope; commit-create will ignore them, but verify that this is intentional.",
        ]
    payload = {
        **document,
        "dry_run": True,
        "would_execute": False,
    }
    return success_envelope("commit-plan", payload, args, env, warnings=list(dict.fromkeys(warnings)))


def handle_commit_create(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    requested_paths = normalize_requested_paths(list(getattr(args, "paths", []) or []))
    if not requested_paths:
        raise CLIUsageError("commit-create requires at least one explicit path.")

    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    args.paths = requested_paths
    document, warnings = build_commit_plan_document(
        runner,
        repo,
        args,
        mode="create",
        require_message=True,
        default_commit=None,
    )
    proposed_change_id = document["message"]["change_id"]
    hook_info = document["hooks"]["commit_msg"]
    validate_commit_create_change_id(
        proposed_change_id,
        hook_info,
        allow_missing_change_id=args.allow_missing_change_id,
    )
    if not document["path_scope"]["entries"]:
        raise CLIUsageError("No local changes were found for the requested paths.")

    if document["path_scope"]["extra_staged_paths"]:
        warnings = [
            *warnings,
            "There are staged changes outside the requested path scope; this command will not include them.",
        ]

    should_execute = args.yes and not args.dry_run
    payload = {
        **document,
        "dry_run": not should_execute,
        "would_execute": should_execute,
        "executed": False,
    }
    if not should_execute:
        warnings = [
            *warnings,
            "commit-create is a high-risk command and defaults to plan mode; pass --yes to execute.",
        ]
        return success_envelope("commit-create", payload, args, env, warnings=list(dict.fromkeys(warnings)))

    repo_root = Path(repo["repo_root"])
    message_info = resolve_message_input(args, runner, repo_root, require_message=True)
    if message_info is None:
        raise CLIUsageError("Provide --message or --message-file.")
    stage_explicit_paths(runner, repo_root, requested_paths)
    result = execute_commit(
        runner,
        repo_root,
        amend=False,
        message_text=message_info["text"],
        paths=requested_paths,
    )
    actual_change_id = analyze_change_id_text(load_commit_message_from_commit(runner, repo_root, "HEAD"), source="HEAD", commit="HEAD")
    payload.update(
        {
            "executed": True,
            "result": result,
            "head_after": {
                "commit": result["head"],
                "short_commit": result["head_short"],
                "subject": result["subject"],
                "change_id": actual_change_id,
            },
        }
    )
    warnings.extend(change_id_warnings(actual_change_id, hook_info=hook_info))
    return success_envelope("commit-create", payload, args, env, warnings=list(dict.fromkeys(warnings)))


def handle_commit_amend(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    if not repo.get("head"):
        raise CLIUsageError("commit-amend requires an existing HEAD commit.")

    requested_paths = normalize_requested_paths(list(getattr(args, "paths", []) or []))
    args.paths = requested_paths
    document, warnings = build_commit_plan_document(
        runner,
        repo,
        args,
        mode="amend",
        require_message=False,
        default_commit="HEAD",
    )
    validate_commit_amend_change_id(
        document["head"]["change_id"],
        document["message"]["change_id"],
        allow_change_id_change=args.allow_change_id_change,
    )
    message_source = document["message"]["source"]
    has_scoped_changes = bool(document["path_scope"]["entries"])
    has_staged_changes = bool(document["status"]["staged"])
    if not requested_paths and not has_staged_changes and message_source == "commit":
        raise CLIUsageError("commit-amend has no staged changes and no new commit message to apply.")
    if requested_paths and not has_scoped_changes and message_source == "commit":
        raise CLIUsageError("No local changes were found for the requested paths and no new commit message was provided.")

    should_execute = args.yes and not args.dry_run
    payload = {
        **document,
        "dry_run": not should_execute,
        "would_execute": should_execute,
        "executed": False,
    }
    if not should_execute:
        warnings = [
            *warnings,
            "commit-amend is a high-risk command and defaults to plan mode; pass --yes to execute.",
        ]
        return success_envelope("commit-amend", payload, args, env, warnings=list(dict.fromkeys(warnings)))

    repo_root = Path(repo["repo_root"])
    message_info = resolve_message_input(args, runner, repo_root, default_commit="HEAD")
    if message_info is None:
        raise CLIUsageError("Could not determine a commit message for commit-amend.")
    if requested_paths:
        stage_explicit_paths(runner, repo_root, requested_paths)
    result = execute_commit(
        runner,
        repo_root,
        amend=True,
        message_text=message_info["text"],
        paths=requested_paths,
    )
    actual_change_id = analyze_change_id_text(load_commit_message_from_commit(runner, repo_root, "HEAD"), source="HEAD", commit="HEAD")
    payload.update(
        {
            "executed": True,
            "result": result,
            "head_after": {
                "commit": result["head"],
                "short_commit": result["head_short"],
                "subject": result["subject"],
                "change_id": actual_change_id,
            },
        }
    )
    warnings.extend(change_id_warnings(actual_change_id, hook_info=document["hooks"]["commit_msg"]))
    return success_envelope("commit-amend", payload, args, env, warnings=list(dict.fromkeys(warnings)))


def handle_push_review_plan(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    enforce_clean_worktree_or_fail(
        repo,
        message="Working tree is not clean. Commit or stash local changes before planning a Gerrit review push.",
    )
    document, warnings = build_push_review_plan_document(runner, repo, args, env)
    payload = {
        **document,
        "mode": "plan",
        "dry_run": True,
        "push_executed": False,
    }
    return success_envelope("push-review-plan", payload, args, env, warnings=list(dict.fromkeys(warnings)))


def handle_push_review(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    repo = inspect_repo(runner)
    enforce_clean_worktree_or_fail(
        repo,
        message="Working tree is not clean. Commit or stash local changes before executing a Gerrit review push.",
    )
    document, warnings = build_push_review_plan_document(runner, repo, args, env)
    repo_root = Path(repo["repo_root"])
    push_timeout = coerce_push_timeout(getattr(args, "push_timeout", None), env, runner.config.timeout_seconds)
    dry_run_mode = not args.yes or bool(args.dry_run)
    push_args = ["push", "--porcelain"]
    if dry_run_mode:
        push_args.append("--dry-run")
    push_args.extend([document["remote"], document["refspec"]])
    result = runner.run(tuple(push_args), cwd=repo_root, timeout=push_timeout)

    if not args.yes:
        warnings = [
            *warnings,
            "push-review defaults to git push --dry-run --porcelain; pass --yes to execute a real push.",
        ]
    if args.yes and args.dry_run:
        warnings = [
            *warnings,
            "Global --dry-run was set, so push-review stayed in dry-run mode even though --yes was provided.",
        ]

    payload = {
        **document,
        "mode": "dry_run" if dry_run_mode else "push",
        "dry_run": dry_run_mode,
        "push_executed": True,
        "push": {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "timeout_seconds": push_timeout,
        },
    }
    return success_envelope("push-review", payload, args, env, warnings=list(dict.fromkeys(warnings)))


def add_push_review_arguments(parser: argparse.ArgumentParser, *, include_push_timeout: bool = False) -> None:
    parser.add_argument("--remote", help="Optional Git remote name. Defaults to Gerrit remote auto-selection.")
    parser.add_argument("--branch", help="Target review branch. Defaults to GERRIT_REVIEW_BRANCH, upstream merge branch, or current branch.")
    parser.add_argument("--topic", help="Optional Gerrit topic to encode in refs/for options.")
    parser.add_argument("--reviewer", action="append", default=[], help="Reviewer email, username, or account id. May be provided multiple times.")
    parser.add_argument("--cc", action="append", default=[], help="CC email, username, or account id. May be provided multiple times.")
    parser.add_argument("--hashtag", action="append", default=[], help="Hashtag to encode in refs/for options. May be provided multiple times.")
    state_group = parser.add_mutually_exclusive_group()
    state_group.add_argument("--wip", action="store_true", help="Push the review as work-in-progress.")
    state_group.add_argument("--ready", action="store_true", help="Mark the review push as ready instead of WIP.")
    if include_push_timeout:
        parser.add_argument(
            "--push-timeout",
            type=float,
            help="Push timeout in seconds. Defaults to GIT_PUSH_TIMEOUT_SECONDS or max(--timeout, 300).",
        )


def doctor_envelope(
    data: Dict[str, Any],
    args: argparse.Namespace,
    env: Mapping[str, str],
    warnings: Sequence[str],
) -> Dict[str, Any]:
    failures = []
    for name, check in data["dependencies"].items():
        if check.get("required") and not check.get("ok"):
            failures.append(f"dependencies.{name}")
    for name, check in data["repository"].items():
        if isinstance(check, Mapping) and check.get("required") and not check.get("ok"):
            failures.append(f"repository.{name}")
    for name, check in data["identity"].items():
        if check.get("required") and not check.get("ok"):
            failures.append(f"identity.{name}")
    doctor_ok = not failures
    data["doctor"] = {
        "ok": doctor_ok,
        "failed_required_checks": failures,
    }
    if doctor_ok:
        return success_envelope("git-doctor", data, args, env, warnings=warnings)
    document = error_envelope(
        "git-doctor",
        "DoctorFailed",
        "One or more required git doctor checks failed.",
        args,
        env,
        hint="Inspect data.doctor.failed_required_checks and each failed check hint.",
        warnings=warnings,
    )
    document["data"] = data
    return document


def handle_git_doctor(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    runner = build_runner(args, env)
    config = runner.config
    git_details = git_binary_details(config)
    warnings = []
    dependencies: Dict[str, Any] = {"git": git_details}
    repository: Dict[str, Any] = {
        "repo": {"ok": False, "required": True},
        "remote": {"ok": False, "required": False},
        "upstream": {"ok": False, "required": False},
    }
    identity: Dict[str, Any] = {
        "user.name": {"ok": False, "required": True, "value": None},
        "user.email": {"ok": False, "required": True, "value": None},
    }
    hooks: Dict[str, Any] = {
        "commit_msg": {"ok": False, "required": False},
    }
    remotes_document: Dict[str, Any] = {"count": 0, "items": []}

    if git_details["ok"]:
        try:
            version_result = runner.run(("--version",), cwd=Path.cwd())
            dependencies["git"]["version"] = version_result.stdout.strip()
        except GitExecutableNotFound as exc:
            dependencies["git"].update({"ok": False, "message": str(exc), "hint": "Install git or set GIT_BIN to a valid executable."})
        probe_cwd = Path(args.repo).expanduser() if args.repo else Path.cwd()
        if probe_cwd.exists() and probe_cwd.is_dir():
            try:
                general_config = load_config_map(runner, probe_cwd)
                identity = config_entries_document(general_config, ("user.name", "user.email"))
                identity["user.name"]["required"] = True
                identity["user.email"]["required"] = True
            except GitCommandError:
                pass

    try:
        repo = inspect_repo(runner)
        config_map = load_config_map(runner, Path(repo["repo_root"]))
        hook_info = commit_msg_hook_info(config_map, Path(repo["repo_root"]), Path(repo["git_dir"]))
        identity = config_entries_document(config_map, ("user.name", "user.email"))
        identity["user.name"]["required"] = True
        identity["user.email"]["required"] = True
        repository = {
            "repo": {
                "ok": True,
                "required": True,
                "repo_root": repo["repo_root"],
                "git_dir": repo["git_dir"],
                "current_branch": repo["current_branch"],
                "head": repo["head_short"],
            },
            "remote": {
                "ok": bool(repo["remotes"]),
                "required": False,
                "count": len(repo["remotes"]),
            },
            "upstream": {
                "ok": bool(repo["upstream"]),
                "required": False,
                "value": repo["upstream"],
            },
        }
        hooks["commit_msg"] = hook_info
        remotes_document = {"count": len(repo["remotes"]), "items": repo["remotes"]}
        warnings.extend(repo_warnings(repo))
        if not repo["remotes"]:
            warnings.append("No Git remotes are configured for this repository.")
        if not hook_info["ok"]:
            warnings.append("commit-msg hook is not installed for this repository.")
        if not identity["user.name"]["ok"]:
            warnings.append("Git user.name is not configured.")
        if not identity["user.email"]["ok"]:
            warnings.append("Git user.email is not configured.")
    except GitConfigError as exc:
        repository["repo"].update(
            {
                "message": str(exc),
                "hint": "Run this command inside a Git repository or pass --repo to a repository root.",
            }
        )
        warnings.append("Repository-scoped Git diagnostics are unavailable until a valid repository is selected.")

    document = {
        "dependencies": dependencies,
        "repository": repository,
        "identity": identity,
        "hooks": hooks,
        "remotes": remotes_document,
    }
    return doctor_envelope(document, args, env, warnings)


def handle_not_implemented(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    command = command_name(args)
    return error_envelope(
        command,
        "NotImplemented",
        f"{command} is registered in the Git CLI skeleton but is not implemented yet.",
        args,
        env,
        hint="Continue with the corresponding M7 task before relying on this command.",
    )


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="git_cli.py",
        description="active-gerrit local Git command line tools",
    )
    parser.add_argument("--repo", help="Git repository path. Defaults to the current working directory.")
    parser.add_argument("--timeout", type=float, help="Git command timeout in seconds. Defaults to GIT_TIMEOUT_SECONDS or 60.")
    parser.add_argument("--trace", help="Optional trace id to include in Git CLI metadata.")
    parser.add_argument("--dry-run", action="store_true", help="Plan write-capable commands without modifying local or remote state.")
    parser.add_argument("--yes", action="store_true", help="Allow a high-risk command to execute after its prechecks pass.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    ping = subparsers.add_parser("ping", help="Validate the Git CLI entrypoint without running git.")
    ping.set_defaults(handler=handle_ping)

    repo_diff = subparsers.add_parser("repo-diff", help="Summarize local staged or unstaged changes.")
    repo_diff.add_argument("--staged", action="store_true", help="Compare the index against HEAD or against --base when provided.")
    repo_diff.add_argument("--base", help="Optional revision to compare against.")
    repo_diff.add_argument("--stat-only", action="store_true", help="Return file and stat summaries without a patch body.")
    repo_diff.add_argument("--include-patch", action="store_true", help="Include the raw diff patch body.")
    repo_diff.set_defaults(handler=handle_repo_diff)

    repo_diff_file = subparsers.add_parser("repo-diff-file", help="Read a single-file local Git diff.")
    repo_diff_file.add_argument("--staged", action="store_true", help="Compare the index against HEAD or against --base when provided.")
    repo_diff_file.add_argument("--base", help="Optional revision to compare against.")
    repo_diff_file.add_argument("--stat-only", action="store_true", help="Return file and stat summaries without a patch body.")
    repo_diff_file.add_argument("path", help="Repository-relative file path.")
    repo_diff_file.set_defaults(handler=handle_repo_diff_file)

    repo_log = subparsers.add_parser("repo-log", help="Read structured recent commit summaries.")
    repo_log.add_argument("--limit", type=int, default=20, help="Maximum number of commits to return.")
    repo_log.add_argument("--revision-range", help="Optional revision range such as HEAD~5..HEAD.")
    repo_log.add_argument("--path", help="Optional path filter.")
    repo_log.set_defaults(handler=handle_repo_log)

    repo_show = subparsers.add_parser("repo-show", help="Read one structured commit summary.")
    repo_show.add_argument("--commit", default="HEAD", help="Commit-ish to inspect. Defaults to HEAD.")
    repo_show.add_argument("--stat-only", action="store_true", help="Return file and stat summaries without a patch body.")
    repo_show.add_argument("--include-patch", action="store_true", help="Include the raw patch body for the selected commit.")
    repo_show.set_defaults(handler=handle_repo_show)

    repo_branches = subparsers.add_parser("repo-branches", help="List local and remote branches.")
    branch_scope = repo_branches.add_mutually_exclusive_group()
    branch_scope.add_argument("--all", action="store_true", help="Include local and remote branches.")
    branch_scope.add_argument("--remotes", action="store_true", help="Only include remote branches.")
    repo_branches.add_argument("--contains", help="Only list branches that contain the given commit.")
    repo_branches.add_argument("--sort", help="Git branch sort key, such as -committerdate or refname.")
    repo_branches.set_defaults(handler=handle_repo_branches)

    fetch_change = subparsers.add_parser("fetch-change", help="Fetch a Gerrit patch set ref into the local repository.")
    fetch_change.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    fetch_change.add_argument(
        "--revision",
        default="current",
        help="Revision id, patch set number, commit SHA, ref, or current.",
    )
    fetch_change.add_argument("--remote", help="Optional Git remote name. Defaults to Gerrit remote auto-selection.")
    fetch_change.add_argument(
        "--fetch-timeout",
        type=float,
        help="Fetch/Gerrit request timeout in seconds. Defaults to GIT_FETCH_TIMEOUT_SECONDS or max(--timeout, 180).",
    )
    fetch_change.set_defaults(handler=handle_fetch_change)

    checkout_change = subparsers.add_parser("checkout-change", help="Check out a fetched Gerrit patch set safely.")
    checkout_change.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    checkout_change.add_argument(
        "--revision",
        default="current",
        help="Revision id, patch set number, commit SHA, ref, or current.",
    )
    checkout_change.add_argument("--remote", help="Optional Git remote name. Defaults to Gerrit remote auto-selection.")
    checkout_change.add_argument("--branch", help="Branch name to create from fetched patch set.")
    checkout_change.add_argument("--detach", action="store_true", help="Detach HEAD at fetched patch set instead of creating a branch.")
    checkout_change.add_argument("--allow-dirty", action="store_true", help="Allow checkout even if the current worktree is dirty.")
    checkout_change.add_argument(
        "--fetch-timeout",
        type=float,
        help="Fetch/Gerrit request timeout in seconds. Defaults to GIT_FETCH_TIMEOUT_SECONDS or max(--timeout, 180).",
    )
    checkout_change.set_defaults(handler=handle_checkout_change)

    worktree_change = subparsers.add_parser("worktree-change", help="Create a dedicated worktree for a Gerrit patch set.")
    worktree_change.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    worktree_change.add_argument(
        "--revision",
        default="current",
        help="Revision id, patch set number, commit SHA, ref, or current.",
    )
    worktree_change.add_argument("--remote", help="Optional Git remote name. Defaults to Gerrit remote auto-selection.")
    worktree_change.add_argument("--branch", help="Branch name to create in the new worktree.")
    worktree_change.add_argument("--detach", action="store_true", help="Detach HEAD in the new worktree.")
    worktree_change.add_argument("--path", help="Target directory for the new worktree.")
    worktree_change.add_argument(
        "--fetch-timeout",
        type=float,
        help="Fetch/Gerrit request timeout in seconds. Defaults to GIT_FETCH_TIMEOUT_SECONDS or max(--timeout, 180).",
    )
    worktree_change.set_defaults(handler=handle_worktree_change)

    change_id_check = subparsers.add_parser("change-id-check", help="Check Change-Id trailers for HEAD or a commit message file.")
    change_id_check_target = change_id_check.add_mutually_exclusive_group()
    change_id_check_target.add_argument("--commit", default="HEAD", help="Commit-ish whose message should be inspected. Defaults to HEAD.")
    change_id_check_target.add_argument("--message-file", help="Path to a commit message file to inspect.")
    change_id_check.set_defaults(handler=handle_change_id_check)

    commit_plan = subparsers.add_parser("commit-plan", help="Summarize files and metadata before creating or amending a commit.")
    commit_plan.add_argument("--amend", action="store_true", help="Plan an amend flow instead of a new commit flow.")
    commit_plan_message = commit_plan.add_mutually_exclusive_group()
    commit_plan_message.add_argument("--message", help="Inline commit message to evaluate.")
    commit_plan_message.add_argument("--message-file", help="Path to a commit message file to evaluate.")
    commit_plan.add_argument("paths", nargs="*", help="Optional explicit path scope for the planned commit.")
    commit_plan.set_defaults(handler=handle_commit_plan)

    commit_create = subparsers.add_parser("commit-create", help="Create a commit from explicit paths.")
    commit_create_message = commit_create.add_mutually_exclusive_group(required=True)
    commit_create_message.add_argument("--message", help="Inline commit message to use for the new commit.")
    commit_create_message.add_argument("--message-file", help="Path to a commit message file to use for the new commit.")
    commit_create.add_argument("--allow-missing-change-id", action="store_true", help="Allow commit execution even when the message lacks a Change-Id and no commit-msg hook is installed.")
    commit_create.add_argument("paths", nargs="+", help="Explicit paths to include in the new commit.")
    commit_create.set_defaults(handler=handle_commit_create)

    commit_amend = subparsers.add_parser("commit-amend", help="Amend the current commit while preserving the Gerrit Change-Id by default.")
    commit_amend_message = commit_amend.add_mutually_exclusive_group()
    commit_amend_message.add_argument("--message", help="Replacement commit message for the amended commit.")
    commit_amend_message.add_argument("--message-file", help="Path to a replacement commit message file.")
    commit_amend.add_argument("--allow-change-id-change", action="store_true", help="Allow the amended commit message to replace or remove the existing Change-Id.")
    commit_amend.add_argument("paths", nargs="*", help="Optional explicit paths to fold into the amended commit.")
    commit_amend.set_defaults(handler=handle_commit_amend)

    push_review_plan = subparsers.add_parser("push-review-plan", help="Build a Gerrit refs/for push plan without updating the remote.")
    add_push_review_arguments(push_review_plan)
    push_review_plan.set_defaults(handler=handle_push_review_plan)

    push_review = subparsers.add_parser("push-review", help="Dry-run or execute a Gerrit review push.")
    add_push_review_arguments(push_review, include_push_timeout=True)
    push_review.set_defaults(handler=handle_push_review)

    explicit_subparsers = {
        "ping",
        "repo-diff",
        "repo-diff-file",
        "repo-log",
        "repo-show",
        "repo-branches",
        "fetch-change",
        "checkout-change",
        "worktree-change",
        "change-id-check",
        "commit-plan",
        "commit-create",
        "commit-amend",
        "push-review-plan",
        "push-review",
    }
    implemented_handlers = {
        "git-doctor": handle_git_doctor,
        "repo-info": handle_repo_info,
        "repo-status": handle_repo_status,
        "repo-remotes": handle_repo_remotes,
        "repo-config": handle_repo_config,
        "repo-diff": handle_repo_diff,
        "repo-diff-file": handle_repo_diff_file,
        "repo-log": handle_repo_log,
        "repo-show": handle_repo_show,
        "repo-branches": handle_repo_branches,
        "fetch-change": handle_fetch_change,
        "checkout-change": handle_checkout_change,
        "worktree-change": handle_worktree_change,
        "change-id-check": handle_change_id_check,
        "commit-plan": handle_commit_plan,
        "commit-create": handle_commit_create,
        "commit-amend": handle_commit_amend,
        "push-review-plan": handle_push_review_plan,
        "push-review": handle_push_review,
    }
    for name, help_text in PLANNED_COMMANDS:
        if name in explicit_subparsers:
            continue
        planned = subparsers.add_parser(name, help=help_text)
        planned.set_defaults(handler=implemented_handlers.get(name, handle_not_implemented))

    return parser


def run(argv: Optional[Sequence[str]] = None, env: Optional[Mapping[str, str]] = None) -> int:
    actual_env = os.environ if env is None else env
    parser = build_parser()
    args: Optional[argparse.Namespace] = None
    try:
        args = parser.parse_args(argv)
        document = args.handler(args, actual_env)
        print_json(document)
        return EXIT_SUCCESS if document.get("ok") else EXIT_FAILURE
    except SystemExit as exc:
        return int(exc.code or 0)
    except CLIUsageError as exc:
        args = fallback_args(args)
        print_json(
            error_envelope(
                command_name(args),
                "ValidationError",
                exc,
                args,
                actual_env,
                hint="Run with --help to inspect available Git commands and options.",
            )
        )
        return EXIT_USAGE
    except GitConfigError as exc:
        args = fallback_args(args)
        print_json(
            error_envelope(
                command_name(args),
                type(exc).__name__,
                exc,
                args,
                actual_env,
                hint="Check local Git configuration, --repo, GIT_BIN, or GIT_TIMEOUT_SECONDS.",
            )
        )
        return EXIT_CONFIG
    except GitCommandError as exc:
        args = fallback_args(args)
        print_json(
            error_envelope(
                command_name(args),
                type(exc).__name__,
                exc,
                args,
                actual_env,
                hint="Inspect the Git command diagnostic and fix the local repository state before retrying.",
            )
        )
        return EXIT_FAILURE
    except GitError as exc:
        args = fallback_args(args)
        print_json(error_envelope(command_name(args), type(exc).__name__, exc, args, actual_env))
        return EXIT_FAILURE
    except Exception as exc:  # pragma: no cover - last-resort safety net.
        args = fallback_args(args)
        print_json(error_envelope(command_name(args), "UnexpectedError", exc, args, actual_env))
        return EXIT_FAILURE


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
