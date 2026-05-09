#!/usr/bin/env python3
"""CLI entry point for active-gerrit-workflow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

SOURCE = "active-gerrit-workflow"
POLICY_VERSION = "review-policies@local"
CORE_REFERENCE_FILES = (
    "business-workflows.md",
    "review-policies.md",
)
OPTIONAL_POLICY_REFERENCE_FILES = (
    "release-policies.md",
    "escalation-rules.md",
)
SECRET_ENV_KEYS = (
    "GERRIT_HTTP_PASSWORD",
    "GERRIT_BEARER_TOKEN",
    "GERRIT_ACCESS_TOKEN",
    "GERRIT_COOKIE",
    "GERRIT_XSRF_TOKEN",
)
DEFAULT_REVIEW_QUEUE_LIMIT = 25
DEFAULT_REVIEW_BRIEF_DIFF_LIMIT = 3
MAX_REVIEW_BRIEF_DIFF_LIMIT = 10
REVIEWED_QUERY_OPTION = "REVIEWED"
RELEASE_BRANCH_MARKERS = (
    "release",
    "stable",
    "hotfix",
)
REVIEW_BRIEF_LARGE_CHURN_THRESHOLD = 80
REVIEW_BRIEF_VERY_LARGE_CHURN_THRESHOLD = 250
SECURITY_PATH_MARKERS = (
    "security",
    "auth",
    "permission",
    "access",
    "acl",
    "secret",
    "token",
    "credential",
    "oauth",
    "login",
    "tls",
    "ssl",
    "crypto",
)
BUILD_PATH_MARKERS = (
    "dockerfile",
    "containerfile",
    "jenkinsfile",
    "makefile",
    "build/",
    "build.gradle",
    "settings.gradle",
    "pom.xml",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    ".github/workflows",
    "ci/",
    "deploy/",
    "release",
    "helm/",
    "k8s/",
)
GENERATED_PATH_MARKERS = (
    "generated",
    "vendor",
    "dist/",
    "bundle/",
    ".min.",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
)
DOCUMENTATION_SUFFIXES = (
    ".md",
    ".rst",
    ".txt",
    ".adoc",
)
METADATA_FILES = {"/COMMIT_MSG"}


class CLIUsageError(Exception):
    """Argument or command usage error."""


class WorkflowError(Exception):
    """Workflow-specific failure with machine-readable metadata."""

    def __init__(
        self,
        message: object,
        *,
        error_type: str,
        hint: Optional[str] = None,
        status: Optional[int] = None,
    ) -> None:
        super().__init__(str(message))
        self.error_type = error_type
        self.hint = hint
        self.status = status


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if status:
            raise CLIUsageError((message or "").strip() or f"argparse exited with status {status}")
        raise SystemExit(status)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def secret_values(env: Mapping[str, str]) -> Iterable[str]:
    for key in SECRET_ENV_KEYS:
        value = env.get(key)
        if value:
            yield value


def redact_message(message: object, env: Mapping[str, str]) -> str:
    text = str(message)
    for secret in secret_values(env):
        text = text.replace(secret, "<redacted>")
    return text


def command_name(args: argparse.Namespace) -> str:
    return getattr(args, "command", None) or "unknown"


def fallback_args(args: Optional[argparse.Namespace]) -> argparse.Namespace:
    return args if args is not None else argparse.Namespace(
        command="unknown",
        trace=None,
        deadline=None,
        no_cache=False,
        refresh=False,
    )


def script_path() -> Path:
    return Path(__file__).resolve()


def workflow_home() -> Path:
    return script_path().parents[1]


def default_active_gerrit_home() -> Path:
    return workflow_home().parent / "active-gerrit"


def configured_active_gerrit_home(env: Mapping[str, str]) -> tuple[Path, str]:
    configured = env.get("ACTIVE_GERRIT_HOME")
    if configured:
        return Path(configured).expanduser().resolve(strict=False), "env"
    return default_active_gerrit_home().resolve(strict=False), "default_sibling"


def resolve_active_gerrit_cli(home: Path) -> tuple[Path, Path]:
    candidates = [home / "scripts" / "gerrit_cli.py"]
    if home.name != "active-gerrit":
        candidates.append(home / "active-gerrit" / "scripts" / "gerrit_cli.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.parents[1], candidate
    raise WorkflowError(
        f"Could not find active-gerrit/scripts/gerrit_cli.py under {home}.",
        error_type="WorkflowConfigError",
        hint="Set ACTIVE_GERRIT_HOME to the active-gerrit directory or the repo root that contains active-gerrit.",
    )


def resolve_active_gerrit_dependency(env: Mapping[str, str]) -> tuple[Path, Path, Path, str]:
    requested_home, active_gerrit_home_source = configured_active_gerrit_home(env)
    active_gerrit_home, active_gerrit_cli = resolve_active_gerrit_cli(requested_home)
    return requested_home, active_gerrit_home, active_gerrit_cli, active_gerrit_home_source


def base_meta(
    args: argparse.Namespace,
    active_gerrit_home: Optional[Path],
    active_gerrit_cli: Optional[Path],
    active_gerrit_home_source: Optional[str],
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "fetched_at": utc_now_iso(),
        "policy_version": POLICY_VERSION,
        "source": SOURCE,
    }
    if active_gerrit_home is not None:
        meta["active_gerrit_home"] = str(active_gerrit_home)
    if active_gerrit_cli is not None:
        meta["active_gerrit_cli"] = str(active_gerrit_cli)
    if active_gerrit_home_source:
        meta["active_gerrit_home_source"] = active_gerrit_home_source
    trace = getattr(args, "trace", None)
    if trace:
        meta["trace"] = trace
    deadline = getattr(args, "deadline", None)
    if deadline:
        meta["deadline"] = deadline
    if getattr(args, "no_cache", False):
        meta["no_cache"] = True
    if getattr(args, "refresh", False):
        meta["refresh"] = True
    return meta


def workflow_report(
    workflow: str,
    ok: bool,
    target: Mapping[str, Any],
    decision_status: str,
    summary: str,
    checks: Sequence[Mapping[str, Any]],
    used_active_gerrit_commands: Sequence[str],
    next_actions: Sequence[str],
    args: argparse.Namespace,
    *,
    needs_human_decision: bool = False,
    warnings: Optional[Sequence[str]] = None,
    error: Optional[Mapping[str, Any]] = None,
    active_gerrit_home: Optional[Path] = None,
    active_gerrit_cli: Optional[Path] = None,
    active_gerrit_home_source: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "workflow": workflow,
        "ok": ok,
        "target": dict(target),
        "decision": {
            "status": decision_status,
            "summary": summary,
            "needs_human_decision": needs_human_decision,
        },
        "checks": [dict(check) for check in checks],
        "used_active_gerrit_commands": list(used_active_gerrit_commands),
        "next_actions": list(next_actions),
        "warnings": list(warnings or []),
        "meta": base_meta(args, active_gerrit_home, active_gerrit_cli, active_gerrit_home_source),
    }
    if error is not None:
        document["error"] = dict(error)
    if extra is not None:
        document.update(dict(extra))
    return document


def error_details(error_type: str, message: object, env: Mapping[str, str], *, hint: Optional[str] = None, status: Optional[int] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": error_type,
        "message": redact_message(message, env),
    }
    if hint:
        payload["hint"] = hint
    if status is not None:
        payload["status"] = status
    return payload


def required_reference_check(references_dir: Optional[Path] = None) -> tuple[Dict[str, Any], bool, bool, List[str]]:
    resolved_references_dir = references_dir if references_dir is not None else workflow_home() / "references"
    missing_core: List[str] = []
    found_core: List[str] = []
    missing_policy: List[str] = []
    found_policy: List[str] = []
    for name in CORE_REFERENCE_FILES:
        path = resolved_references_dir / name
        if path.is_file():
            found_core.append(str(path))
        else:
            missing_core.append(str(path))
    for name in OPTIONAL_POLICY_REFERENCE_FILES:
        path = resolved_references_dir / name
        if path.is_file():
            found_policy.append(str(path))
        else:
            missing_policy.append(str(path))
    if missing_core:
        return (
            {
                "name": "workflow_references",
                "status": "failed",
                "evidence": [
                    "Missing required workflow reference files.",
                    *missing_core,
                ],
            },
            False,
            False,
            ["Restore the required workflow reference files before using workflow commands."],
        )
    if missing_policy:
        return (
            {
                "name": "workflow_references",
                "status": "warning",
                "evidence": [
                    "Core workflow references are present, but some policy references are missing.",
                    *found_core,
                    *found_policy,
                    *missing_policy,
                ],
                "details": {
                    "missing_policy_references": missing_policy,
                    "found_policy_references": found_policy,
                },
            },
            True,
            False,
            [
                "Add or restore the optional policy reference files before treating workflow policy as complete.",
            ],
        )
    return (
        {
            "name": "workflow_references",
            "status": "passed",
            "evidence": [
                "Required workflow references are present.",
                *found_core,
                *found_policy,
            ],
        },
        True,
        True,
        [],
    )


def apply_reference_policy_result(
    decision_status: str,
    summary: str,
    *,
    core_references_ok: bool,
    policy_references_complete: bool,
    blocked_summary: str,
    human_gap_summary: str,
    base_needs_human_decision: bool = False,
) -> tuple[bool, str, str, bool]:
    if not core_references_ok:
        return False, "blocked", blocked_summary, base_needs_human_decision
    needs_human_decision = base_needs_human_decision or not policy_references_complete
    if policy_references_complete:
        return True, decision_status, summary, needs_human_decision
    adjusted_status = "warning" if decision_status == "pass" else decision_status
    adjusted_summary = human_gap_summary if not summary else f"{summary.rstrip('.')} ; {human_gap_summary}".replace(" ;", ";")
    return True, adjusted_status, adjusted_summary, needs_human_decision


def workflow_cli_check() -> Dict[str, Any]:
    return {
        "name": "workflow_cli",
        "status": "passed",
        "evidence": [f"Workflow CLI entrypoint resolved at {script_path()}."],
    }


def build_active_gerrit_command(args: argparse.Namespace, cli_path: Path, command: str, extra_args: Sequence[str] = ()) -> List[str]:
    invocation = [sys.executable, str(cli_path)]
    if getattr(args, "trace", None):
        invocation.extend(["--trace", args.trace])
    if getattr(args, "deadline", None):
        invocation.extend(["--deadline", args.deadline])
    if getattr(args, "no_cache", False):
        invocation.append("--no-cache")
    if getattr(args, "refresh", False):
        invocation.append("--refresh")
    invocation.append(command)
    invocation.extend(extra_args)
    return invocation


def run_active_gerrit_command(
    args: argparse.Namespace,
    env: Mapping[str, str],
    cli_path: Path,
    command: str,
    extra_args: Sequence[str] = (),
) -> Dict[str, Any]:
    invocation = build_active_gerrit_command(args, cli_path, command, extra_args)
    try:
        completed = subprocess.run(
            invocation,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=dict(env),
        )
    except OSError as exc:
        raise WorkflowError(
            exc,
            error_type="WorkflowExecutionError",
            hint="Verify that Python can execute the active-gerrit CLI from the resolved ACTIVE_GERRIT_HOME.",
        ) from exc

    stdout = (completed.stdout or "").strip()
    stderr = redact_message((completed.stderr or "").strip(), env)
    if not stdout:
        raise WorkflowError(
            f"active-gerrit {command} produced no JSON output.",
            error_type="WorkflowExecutionError",
            hint="Inspect active-gerrit stderr and confirm the command prints a single JSON document to stdout.",
        )

    try:
        document = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError(
            f"active-gerrit {command} returned invalid JSON: {exc}",
            error_type="WorkflowExecutionError",
            hint="Fix the active-gerrit command so it emits one valid JSON document to stdout.",
        ) from exc

    return {
        "argv": invocation,
        "document": document,
        "returncode": completed.returncode,
        "stderr": stderr,
    }


def summarize_active_gerrit_failure(document: Mapping[str, Any], returncode: int, stderr: str) -> str:
    error = document.get("error") if isinstance(document.get("error"), Mapping) else None
    if error and error.get("message"):
        return f"active-gerrit doctor failed: {error['message']}"
    if stderr:
        return f"active-gerrit doctor failed with stderr: {stderr}"
    return f"active-gerrit doctor failed with exit code {returncode}."


def active_gerrit_path_check(active_gerrit_home: Path, active_gerrit_cli: Path, source: str) -> Dict[str, Any]:
    if source == "env":
        source_message = "ACTIVE_GERRIT_HOME was provided explicitly."
    else:
        source_message = "Resolved active-gerrit from the default sibling layout."
    return {
        "name": "active_gerrit_cli",
        "status": "passed",
        "evidence": [
            source_message,
            f"Resolved active-gerrit home at {active_gerrit_home}.",
            f"Resolved active-gerrit CLI at {active_gerrit_cli}.",
        ],
    }


def extend_active_gerrit_warnings(
    warnings: List[str],
    command: str,
    base_document: Mapping[str, Any],
    stderr: str,
) -> None:
    base_warnings = base_document.get("warnings") if isinstance(base_document.get("warnings"), list) else []
    for warning in base_warnings:
        warnings.append(f"active-gerrit {command}: {warning}")
    if stderr:
        warnings.append(f"active-gerrit {command} stderr: {stderr}")


def summarize_active_gerrit_failure(command: str, document: Mapping[str, Any], returncode: int, stderr: str) -> str:
    error = document.get("error") if isinstance(document.get("error"), Mapping) else None
    if error and error.get("message"):
        return f"active-gerrit {command} failed: {error['message']}"
    if stderr:
        return f"active-gerrit {command} failed with stderr: {stderr}"
    return f"active-gerrit {command} failed with exit code {returncode}."


def active_gerrit_error_payload(
    command: str,
    document: Mapping[str, Any],
    returncode: int,
    stderr: str,
    env: Mapping[str, str],
) -> Dict[str, Any]:
    error_payload = document.get("error") if isinstance(document.get("error"), Mapping) else {}
    summary = summarize_active_gerrit_failure(command, document, returncode, stderr)
    return error_details(
        str(error_payload.get("type") or "WorkflowDependencyError"),
        error_payload.get("message") or summary,
        env,
        hint=error_payload.get("hint") if isinstance(error_payload.get("hint"), str) else None,
        status=error_payload.get("status") if isinstance(error_payload.get("status"), int) else None,
    )


def extract_change_summaries(document: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    data = document.get("data")
    if isinstance(data, list):
        changes = data
    elif isinstance(data, Mapping):
        changes = data.get("changes")
    else:
        changes = None
    if not isinstance(changes, list):
        raise WorkflowError(
            "active-gerrit query-preset returned an invalid payload without data.changes.",
            error_type="WorkflowExecutionError",
            hint="Ensure active-gerrit query-preset returns data as a change array or as an object with data.changes.",
        )
    normalized_changes: List[Mapping[str, Any]] = []
    for change in changes:
        if not isinstance(change, Mapping):
            raise WorkflowError(
                "active-gerrit query-preset returned a non-object change entry.",
                error_type="WorkflowExecutionError",
                hint="Ensure each active-gerrit change summary is a JSON object.",
            )
        normalized_changes.append(change)
    return normalized_changes


def display_account(account: object) -> str:
    if not isinstance(account, Mapping):
        return "unknown"
    for field in ("username", "name", "email", "account_id", "_account_id"):
        value = account.get(field)
        if value:
            return str(value)
    return "unknown"


def is_release_branch(branch: object) -> bool:
    if not isinstance(branch, str) or not branch:
        return False
    normalized = branch.lower()
    if normalized.startswith(RELEASE_BRANCH_MARKERS):
        return True
    return any(f"/{marker}" in normalized for marker in RELEASE_BRANCH_MARKERS)


def unresolved_comment_count(change: Mapping[str, Any]) -> int:
    value = change.get("unresolved_comment_count", 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def review_queue_flags(change: Mapping[str, Any]) -> Dict[str, bool]:
    unresolved_count = unresolved_comment_count(change)
    return {
        "work_in_progress": bool(change.get("work_in_progress", False)),
        "is_private": bool(change.get("is_private", False)),
        "has_unresolved_comments": unresolved_count > 0,
        "needs_my_response": not bool(change.get("reviewed", False)),
        "release_branch": is_release_branch(change.get("branch")),
    }


def review_queue_priority_reasons(flags: Mapping[str, bool]) -> List[str]:
    reasons: List[str] = []
    if flags.get("needs_my_response"):
        reasons.append("missing_current_user_response")
    if flags.get("has_unresolved_comments"):
        reasons.append("unresolved_comments")
    if flags.get("is_private"):
        reasons.append("private_change")
    if flags.get("work_in_progress"):
        reasons.append("work_in_progress")
    if flags.get("release_branch"):
        reasons.append("release_branch")
    if not reasons:
        reasons.append("standard_review")
    return reasons


def review_queue_next_action(flags: Mapping[str, bool]) -> str:
    if flags.get("work_in_progress"):
        return "skip_wip_until_owner_marks_ready"
    if flags.get("is_private"):
        return "confirm_private_context_before_reviewing"
    if flags.get("needs_my_response") and flags.get("has_unresolved_comments"):
        return "inspect_latest_patch_set_and_reply_to_unresolved_threads"
    if flags.get("needs_my_response"):
        return "inspect_latest_patch_set_and_respond"
    if flags.get("has_unresolved_comments"):
        return "ask_owner_for_clarification_on_unresolved_threads"
    if flags.get("release_branch"):
        return "inspect_release_branch_risk_before_voting"
    return "inspect_diff_and_decide_whether_to_comment_or_vote"


def build_review_queue_item(change: Mapping[str, Any]) -> Dict[str, Any]:
    flags = review_queue_flags(change)
    owner = change.get("owner") if isinstance(change.get("owner"), Mapping) else {}
    labels = change.get("labels") if isinstance(change.get("labels"), Mapping) else {}
    return {
        "change": change.get("id"),
        "number": change.get("number"),
        "triplet_id": change.get("triplet_id"),
        "project": change.get("project"),
        "branch": change.get("branch"),
        "subject": change.get("subject"),
        "status": change.get("status"),
        "owner": dict(owner),
        "updated": change.get("updated"),
        "labels": dict(labels),
        "topic": change.get("topic"),
        "hashtags": list(change.get("hashtags") or []),
        "unresolved_comment_count": unresolved_comment_count(change),
        "flags": flags,
        "priority_reasons": review_queue_priority_reasons(flags),
        "next_action": review_queue_next_action(flags),
        "evidence": [
            f"change={change.get('id')}",
            f"owner={display_account(owner)}",
            f"branch={change.get('branch')}",
            f"updated={change.get('updated')}",
            f"unresolved_comment_count={unresolved_comment_count(change)}",
        ],
    }


def sort_review_queue_changes(changes: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(
        changes,
        key=lambda change: (
            str(change.get("updated") or ""),
            str(change.get("id") or ""),
        ),
    )


def build_review_queue_summary(items: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "total_changes": len(items),
        "needs_my_response_changes": sum(1 for item in items if item["flags"].get("needs_my_response")),
        "changes_with_unresolved_comments": sum(1 for item in items if item["flags"].get("has_unresolved_comments")),
        "unresolved_comment_threads": sum(int(item.get("unresolved_comment_count", 0)) for item in items),
        "work_in_progress_changes": sum(1 for item in items if item["flags"].get("work_in_progress")),
        "private_changes": sum(1 for item in items if item["flags"].get("is_private")),
        "release_branch_changes": sum(1 for item in items if item["flags"].get("release_branch")),
    }


def review_queue_decision(summary: Mapping[str, int]) -> tuple[str, str]:
    total = int(summary.get("total_changes", 0))
    if total == 0:
        return "pass", "No open changes are currently waiting for your review."

    highlights: List[str] = []
    needs_response = int(summary.get("needs_my_response_changes", 0))
    unresolved_changes = int(summary.get("changes_with_unresolved_comments", 0))
    work_in_progress_changes = int(summary.get("work_in_progress_changes", 0))
    private_changes = int(summary.get("private_changes", 0))
    release_branch_changes = int(summary.get("release_branch_changes", 0))
    if needs_response:
        highlights.append(f"{needs_response} still need your response")
    if unresolved_changes:
        highlights.append(f"{unresolved_changes} have unresolved comment threads")
    if work_in_progress_changes:
        highlights.append(f"{work_in_progress_changes} are WIP")
    if private_changes:
        highlights.append(f"{private_changes} are private")
    if release_branch_changes:
        highlights.append(f"{release_branch_changes} target release-style branches")
    if highlights:
        return "warning", f"{total} open changes are waiting for review; " + "; ".join(highlights) + "."
    return "pass", f"{total} open changes are queued for review with no immediate workflow flags."


def build_review_queue_next_actions(summary: Mapping[str, int]) -> List[str]:
    total = int(summary.get("total_changes", 0))
    if total == 0:
        return ["Run my-review-queue again after new review requests arrive."]

    actions: List[str] = ["Review the oldest open change first and continue in updated-time order."]
    if int(summary.get("needs_my_response_changes", 0)):
        actions.append("Start with changes where Gerrit reports your response is still missing.")
    if int(summary.get("changes_with_unresolved_comments", 0)):
        actions.append("Inspect unresolved comment threads before casting votes.")
    if int(summary.get("private_changes", 0)):
        actions.append("Confirm private changes have the right visibility and context before proceeding.")
    if int(summary.get("work_in_progress_changes", 0)):
        actions.append("Skip WIP changes until the owner marks them ready for review.")
    if int(summary.get("release_branch_changes", 0)):
        actions.append("Treat release branch changes as higher risk during review triage.")
    return actions


def coerce_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def unique_strings(values: Iterable[object]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value is None:
            continue
        text = value.strip() if isinstance(value, str) else str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def path_has_marker(path: object, markers: Sequence[str]) -> bool:
    normalized = string_value(path).lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in markers)


def is_metadata_file(path: object) -> bool:
    return string_value(path) in METADATA_FILES


def is_test_file(path: object) -> bool:
    normalized = string_value(path).lower()
    if not normalized:
        return False
    filename = normalized.rsplit("/", 1)[-1]
    return any(
        marker in normalized
        for marker in (
            "/test/",
            "/tests/",
            "/spec/",
            "/specs/",
            "/__tests__/",
        )
    ) or filename.startswith("test_") or filename.endswith(
        (
            "_test.py",
            "_test.go",
            "_test.rs",
            ".spec.js",
            ".spec.ts",
            "test.java",
        )
    )


def is_documentation_file(path: object) -> bool:
    normalized = string_value(path).lower()
    if not normalized:
        return False
    return normalized.startswith("doc/") or normalized.startswith("docs/") or normalized.endswith(DOCUMENTATION_SUFFIXES)


def is_generated_file(path: object) -> bool:
    return path_has_marker(path, GENERATED_PATH_MARKERS)


def changed_lines(file_entry: Mapping[str, Any]) -> int:
    return coerce_int(file_entry.get("lines_inserted")) + coerce_int(file_entry.get("lines_deleted"))


def file_area(path: object) -> str:
    normalized = string_value(path).strip("/")
    if not normalized:
        return "root"
    parts = normalized.split("/")
    if len(parts) == 1:
        return parts[0]
    if parts[0] in {"src", "tests", "test", "pkg", "cmd", "lib", "app"}:
        return "/".join(parts[:2])
    return parts[0]


def extract_change_detail(document: Mapping[str, Any]) -> Mapping[str, Any]:
    data = document.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("summary"), Mapping):
        raise WorkflowError(
            "active-gerrit get-change returned an invalid payload without data.summary.",
            error_type="WorkflowExecutionError",
            hint="Ensure active-gerrit get-change returns a ChangeDetail object in data.",
        )
    return data


def extract_file_listing(document: Mapping[str, Any]) -> Mapping[str, Any]:
    data = document.get("data")
    files = data.get("files") if isinstance(data, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(files, list):
        raise WorkflowError(
            "active-gerrit list-files returned an invalid payload without data.files.",
            error_type="WorkflowExecutionError",
            hint="Ensure active-gerrit list-files returns an object with a files array in data.",
        )
    for file_entry in files:
        if not isinstance(file_entry, Mapping):
            raise WorkflowError(
                "active-gerrit list-files returned a non-object file entry.",
                error_type="WorkflowExecutionError",
                hint="Ensure each active-gerrit file summary is a JSON object.",
            )
    return data


def extract_file_diff(document: Mapping[str, Any]) -> Mapping[str, Any]:
    data = document.get("data")
    if not isinstance(data, Mapping):
        raise WorkflowError(
            "active-gerrit get-diff returned an invalid payload without data.",
            error_type="WorkflowExecutionError",
            hint="Ensure active-gerrit get-diff returns a FileDiff object in data.",
        )
    return data


def extract_submit_plan(document: Mapping[str, Any]) -> Mapping[str, Any]:
    data = document.get("data")
    checks = data.get("checks") if isinstance(data, Mapping) else None
    blockers = data.get("blockers") if isinstance(data, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(checks, list) or not isinstance(blockers, list):
        raise WorkflowError(
            "active-gerrit submit --dry-run returned an invalid payload without plan checks and blockers.",
            error_type="WorkflowExecutionError",
            hint="Ensure active-gerrit submit --dry-run returns the submit precheck plan in data.",
        )
    return data


def build_review_brief_file_risk(file_entry: Mapping[str, Any]) -> Dict[str, Any]:
    path = string_value(file_entry.get("file"))
    status = string_value(file_entry.get("status"))
    lines_inserted = coerce_int(file_entry.get("lines_inserted"))
    lines_deleted = coerce_int(file_entry.get("lines_deleted"))
    total_changed_lines = lines_inserted + lines_deleted
    test_file = is_test_file(path)
    documentation_file = is_documentation_file(path)
    generated_file = is_generated_file(path)
    metadata_file = is_metadata_file(path)

    risk_score = 0
    risk_reasons: List[str] = []
    categories: List[str] = []

    if total_changed_lines >= REVIEW_BRIEF_VERY_LARGE_CHURN_THRESHOLD:
        risk_score += 5
        risk_reasons.append("very_large_churn")
    elif total_changed_lines >= REVIEW_BRIEF_LARGE_CHURN_THRESHOLD:
        risk_score += 3
        risk_reasons.append("large_churn")
    elif total_changed_lines >= 25:
        risk_score += 1
        risk_reasons.append("moderate_churn")

    if not test_file and path_has_marker(path, SECURITY_PATH_MARKERS):
        risk_score += 5
        risk_reasons.append("security_sensitive_path")
        categories.append("security")
    if not test_file and path_has_marker(path, BUILD_PATH_MARKERS):
        risk_score += 5
        risk_reasons.append("build_or_config_path")
        categories.append("config")
    if not test_file and generated_file:
        risk_score += 3
        risk_reasons.append("generated_or_vendor_artifact")
        categories.append("generated")
    if status in {"A", "D", "R", "C"}:
        risk_score += 2
        risk_reasons.append("non_trivial_file_status")
    if test_file:
        categories.append("test")
    if documentation_file:
        categories.append("documentation")
    if not metadata_file and not test_file and not documentation_file:
        risk_score += 1

    return {
        "file": path,
        "status": status or "M",
        "old_path": file_entry.get("old_path"),
        "lines_inserted": lines_inserted,
        "lines_deleted": lines_deleted,
        "lines_changed": total_changed_lines,
        "size_delta": coerce_int(file_entry.get("size_delta")),
        "area": file_area(path),
        "risk_score": risk_score,
        "risk_reasons": risk_reasons or ["standard_file"],
        "categories": sorted(set(categories)),
        "is_test_file": test_file,
        "is_documentation_file": documentation_file,
        "is_generated_file": generated_file,
        "is_metadata_file": metadata_file,
    }


def build_review_brief_overview(
    change_summary: Mapping[str, Any],
    change_detail: Mapping[str, Any],
    file_listing: Mapping[str, Any],
    file_risks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    reviewable_files = [item for item in file_risks if not bool(item.get("is_metadata_file"))]
    areas: Dict[str, int] = {}
    for item in reviewable_files:
        area = string_value(item.get("area")) or "root"
        areas[area] = areas.get(area, 0) + 1

    messages = change_detail.get("messages") if isinstance(change_detail.get("messages"), list) else []
    code_files_changed = sum(
        1
        for item in reviewable_files
        if not bool(item.get("is_test_file")) and not bool(item.get("is_documentation_file"))
    )
    test_files_changed = sum(1 for item in reviewable_files if bool(item.get("is_test_file")))
    return {
        "requested_revision": file_listing.get("requested_revision"),
        "revision": file_listing.get("revision"),
        "revision_sha": file_listing.get("revision_sha"),
        "patch_set": file_listing.get("patch_set"),
        "files_changed": len(reviewable_files),
        "metadata_files_changed": len(file_risks) - len(reviewable_files),
        "lines_inserted": sum(coerce_int(item.get("lines_inserted")) for item in reviewable_files),
        "lines_deleted": sum(coerce_int(item.get("lines_deleted")) for item in reviewable_files),
        "top_areas": [
            area
            for area, _ in sorted(areas.items(), key=lambda entry: (-entry[1], entry[0]))[:3]
        ],
        "code_files_changed": code_files_changed,
        "test_files_changed": test_files_changed,
        "documentation_files_changed": sum(1 for item in reviewable_files if bool(item.get("is_documentation_file"))),
        "generated_files_changed": sum(1 for item in reviewable_files if bool(item.get("is_generated_file"))),
        "unresolved_comment_count": unresolved_comment_count(change_summary),
        "message_count": len(messages),
        "test_gap": code_files_changed > 0 and test_files_changed == 0,
        "work_in_progress": bool(change_summary.get("work_in_progress", False)),
        "is_private": bool(change_summary.get("is_private", False)),
    }


def select_review_brief_files(file_risks: Sequence[Mapping[str, Any]], limit: int) -> List[Mapping[str, Any]]:
    candidates = [item for item in file_risks if not bool(item.get("is_metadata_file"))]
    return sorted(
        candidates,
        key=lambda item: (
            -coerce_int(item.get("risk_score")),
            -coerce_int(item.get("lines_changed")),
            string_value(item.get("file")),
        ),
    )[:limit]


def diff_line_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def summarize_review_brief_diff(diff: Mapping[str, Any]) -> Dict[str, Any]:
    content = diff.get("content") if isinstance(diff.get("content"), list) else []
    diff_header = diff.get("diff_header") if isinstance(diff.get("diff_header"), list) else []
    warnings = [str(item) for item in (diff.get("warnings") or []) if item]
    approximate_added_lines = 0
    approximate_deleted_lines = 0
    chunk_count = 0
    for chunk in content:
        if not isinstance(chunk, Mapping):
            continue
        chunk_count += 1
        approximate_added_lines += diff_line_count(chunk.get("b")) + diff_line_count(chunk.get("edit_b"))
        approximate_deleted_lines += diff_line_count(chunk.get("a")) + diff_line_count(chunk.get("edit_a"))
    meta_a = diff.get("meta_a") if isinstance(diff.get("meta_a"), Mapping) else {}
    meta_b = diff.get("meta_b") if isinstance(diff.get("meta_b"), Mapping) else {}
    return {
        "change_type": diff.get("change_type"),
        "content_type": meta_b.get("content_type") or meta_a.get("content_type"),
        "chunk_count": chunk_count,
        "approximate_added_lines": approximate_added_lines,
        "approximate_deleted_lines": approximate_deleted_lines,
        "header_preview": diff_header[:3],
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def review_focus_for_file(file_risk: Mapping[str, Any]) -> str:
    categories = set(file_risk.get("categories") or [])
    reasons = set(file_risk.get("risk_reasons") or [])
    if "security" in categories:
        return "Validate auth, permission, token, and secret handling in this path."
    if "config" in categories:
        return "Check build, release, and deployment assumptions before voting."
    if "generated" in categories:
        return "Verify the source-of-truth change and regeneration workflow."
    if "test" in categories:
        return "Use this test diff to confirm the intended behavior change."
    if "very_large_churn" in reasons or "large_churn" in reasons:
        return "Read this file in smaller hunks and confirm the control-flow changes."
    return "Inspect the main code path and confirm the change matches the stated intent."


def build_review_brief_risk_areas(file_risks: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    ranked = select_review_brief_files(file_risks, 5)
    return [
        {
            "file": item.get("file"),
            "status": item.get("status"),
            "area": item.get("area"),
            "lines_changed": item.get("lines_changed"),
            "risk_score": item.get("risk_score"),
            "risk_reasons": list(item.get("risk_reasons") or []),
            "categories": list(item.get("categories") or []),
        }
        for item in ranked
    ]


def reviewer_state_accounts(reviewers: object, state: str) -> List[Mapping[str, Any]]:
    if not isinstance(reviewers, Mapping):
        return []
    accounts = reviewers.get(state)
    if not isinstance(accounts, Sequence) or isinstance(accounts, (str, bytes, bytearray)):
        return []
    return [account for account in accounts if isinstance(account, Mapping)]


def reviewer_state_counts(reviewers: object) -> Dict[str, int]:
    return {
        "REVIEWER": len(reviewer_state_accounts(reviewers, "REVIEWER")),
        "CC": len(reviewer_state_accounts(reviewers, "CC")),
        "REMOVED": len(reviewer_state_accounts(reviewers, "REMOVED")),
    }


def label_is_approved(labels: object, label_name: str) -> bool:
    if not isinstance(labels, Mapping):
        return False
    label = labels.get(label_name)
    return isinstance(label, Mapping) and isinstance(label.get("approved"), Mapping)


def build_pre_submit_change_snapshot(change_summary: Mapping[str, Any]) -> Dict[str, Any]:
    owner = change_summary.get("owner") if isinstance(change_summary.get("owner"), Mapping) else {}
    return {
        "id": change_summary.get("id"),
        "number": change_summary.get("number"),
        "project": change_summary.get("project"),
        "branch": change_summary.get("branch"),
        "subject": change_summary.get("subject"),
        "status": change_summary.get("status"),
        "owner": dict(owner),
        "updated": change_summary.get("updated"),
        "current_patch_set": change_summary.get("current_patch_set"),
        "topic": change_summary.get("topic"),
        "hashtags": list(change_summary.get("hashtags") or []),
        "work_in_progress": bool(change_summary.get("work_in_progress", False)),
        "is_private": bool(change_summary.get("is_private", False)),
        "unresolved_comment_count": unresolved_comment_count(change_summary),
    }


def build_pre_submit_business_assessment(
    change_summary: Mapping[str, Any],
    change_detail: Mapping[str, Any],
    submit_plan: Mapping[str, Any],
    overview: Mapping[str, Any],
    file_risks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    business_blockers: List[str] = []
    business_warnings: List[str] = []
    next_actions: List[str] = []
    human_decision_items: List[str] = []

    branch_name = string_value(change_summary.get("branch"))
    owner = change_summary.get("owner") if isinstance(change_summary.get("owner"), Mapping) else {}
    owner_name = display_account(owner)
    reviewers = change_detail.get("reviewers") if isinstance(change_detail.get("reviewers"), Mapping) else {}
    reviewer_counts = reviewer_state_counts(reviewers)
    review_accounts = reviewer_state_accounts(reviewers, "REVIEWER")
    labels = change_summary.get("labels") if isinstance(change_summary.get("labels"), Mapping) else {}
    visible_labels = sorted(str(name) for name in labels.keys())
    code_review_approved = label_is_approved(labels, "Code-Review")
    verified_visible = "Verified" in labels
    verified_approved = label_is_approved(labels, "Verified") if verified_visible else None
    high_risk_files = [
        item
        for item in build_review_brief_risk_areas(file_risks)
        if coerce_int(item.get("risk_score")) >= 5
    ]

    if bool(change_summary.get("work_in_progress", False)):
        message = "Change is still marked Work In Progress."
        checks.append({"name": "workflow_state", "status": "failed", "evidence": [message]})
        business_blockers.append(message)
        next_actions.append("Wait for the owner to mark the change ready before submit.")
    else:
        checks.append({"name": "workflow_state", "status": "passed", "evidence": ["Change is not marked WIP."]})

    if bool(change_summary.get("is_private", False)):
        message = "Change is private; confirm the intended visibility before submit."
        checks.append({"name": "visibility_policy", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        human_decision_items.append("Private change visibility must be confirmed by a human before submit.")
        next_actions.append("Confirm that the private change is visible to the intended submitters and reviewers.")
    else:
        checks.append({"name": "visibility_policy", "status": "passed", "evidence": ["Change is not private."]})

    if not branch_name:
        message = "Target branch is missing from change detail."
        checks.append({"name": "branch_policy", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        human_decision_items.append("Target branch is missing; confirm that this change is on the intended branch.")
    elif is_release_branch(branch_name):
        message = f"Target branch {branch_name} matches release-style branch markers."
        checks.append({"name": "branch_policy", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        human_decision_items.append(f"Release branch {branch_name} requires explicit human confirmation before submit.")
        next_actions.append("Confirm release branch scope, rollback plan, and reviewer sign-off before submit.")
    else:
        checks.append(
            {
                "name": "branch_policy",
                "status": "passed",
                "evidence": [f"Target branch {branch_name} is not a release-style branch."],
            }
        )

    if owner_name == "unknown":
        message = "Owner identity is missing or incomplete in change detail."
        checks.append({"name": "owner_policy", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        human_decision_items.append("Owner identity is incomplete; confirm ownership before submit.")
    else:
        checks.append({"name": "owner_policy", "status": "passed", "evidence": [f"Owner is {owner_name}."]})

    if reviewer_counts["REVIEWER"] < 1:
        message = "No REVIEWER entries are visible on the change."
        checks.append({"name": "reviewer_policy", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        human_decision_items.append("Reviewer assignment is unclear; confirm who reviewed this change before submit.")
        next_actions.append("Add or confirm at least one reviewer before submitting.")
    else:
        checks.append(
            {
                "name": "reviewer_policy",
                "status": "passed",
                "evidence": [
                    f"{reviewer_counts['REVIEWER']} reviewer(s) are assigned: "
                    + ", ".join(display_account(account) for account in review_accounts[:3])
                    + ("." if reviewer_counts["REVIEWER"] <= 3 else ", ...")
                ],
            }
        )

    label_evidence: List[str] = []
    if code_review_approved:
        label_evidence.append("Code-Review shows an approved value in change detail.")
    else:
        label_evidence.append(
            "No approved Code-Review label is visible in change detail."
            if "Code-Review" in labels
            else "Change detail does not expose a Code-Review label."
        )
    if verified_visible and not verified_approved:
        label_evidence.append("Verified is visible but not approved in change detail.")
    if code_review_approved and (verified_approved is not False):
        checks.append({"name": "label_policy", "status": "passed", "evidence": label_evidence})
    else:
        checks.append(
            {
                "name": "label_policy",
                "status": "warning",
                "evidence": label_evidence,
                "details": {"visible_labels": visible_labels},
            }
        )
        business_warnings.extend(label_evidence)
        human_decision_items.append("Confirm that the required submit labels are present under local policy.")
        next_actions.append("Confirm label state in Gerrit before submitting, especially Code-Review.")

    unresolved_count = coerce_int(overview.get("unresolved_comment_count"))
    if unresolved_count:
        message = f"{unresolved_count} unresolved comment thread(s) remain on the change."
        checks.append({"name": "unresolved_comments", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        next_actions.append("Resolve or explicitly waive unresolved comments before submit.")
    else:
        checks.append({"name": "unresolved_comments", "status": "passed", "evidence": ["No unresolved comment threads are reported."]})

    if bool(overview.get("test_gap")):
        message = "Code files changed without a corresponding test file change."
        checks.append({"name": "test_evidence", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        next_actions.append("Request CI or manual test evidence before submit.")
        if branch_name and is_release_branch(branch_name):
            human_decision_items.append("Release branch change is missing test evidence; a human must decide whether to proceed.")
    else:
        checks.append(
            {
                "name": "test_evidence",
                "status": "passed",
                "evidence": [
                    f"Test evidence is present in changed files (test_files_changed={coerce_int(overview.get('test_files_changed'))})."
                ],
            }
        )

    if high_risk_files:
        risk_paths = [string_value(item.get("file")) for item in high_risk_files[:3] if string_value(item.get("file"))]
        message = "High-risk files changed: " + ", ".join(risk_paths) + "."
        checks.append({"name": "file_risk", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        next_actions.append("Inspect high-risk paths before submit: " + ", ".join(risk_paths) + ".")
    else:
        checks.append({"name": "file_risk", "status": "passed", "evidence": ["No high-risk file paths were detected in the changed files."]})

    submitted_together = submit_plan.get("submitted_together") if isinstance(submit_plan.get("submitted_together"), Mapping) else {}
    submitted_together_changes = submitted_together.get("changes") if isinstance(submitted_together.get("changes"), list) else []
    non_new_companions: List[str] = []
    current_change_id = string_value(change_summary.get("id"))
    for change in submitted_together_changes:
        if not isinstance(change, Mapping):
            continue
        change_id = string_value(change.get("id")) or str(change.get("_number") or "unknown")
        if change_id == current_change_id:
            continue
        status = string_value(change.get("status"))
        if status and status != "NEW":
            non_new_companions.append(f"{change_id}:{status}")
    non_visible_changes = coerce_int(submitted_together.get("non_visible_changes"))
    total_together = coerce_int(submitted_together.get("total_count"))
    if non_new_companions:
        message = "Submitted-together includes non-NEW companion changes: " + ", ".join(non_new_companions) + "."
        checks.append({"name": "submitted_together_policy", "status": "failed", "evidence": [message]})
        business_blockers.append("Submitted-together includes companion changes that are not NEW.")
        next_actions.append("Review submitted-together companion changes and clear their blockers before submit.")
    elif non_visible_changes > 0:
        message = f"{non_visible_changes} submitted-together change(s) are not visible to the current user."
        checks.append({"name": "submitted_together_policy", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        human_decision_items.append("Submitted-together scope is partially hidden; confirm what else would be submitted.")
    elif total_together > 1:
        message = f"Submit would include {total_together - 1} additional visible change(s)."
        checks.append({"name": "submitted_together_policy", "status": "warning", "evidence": [message]})
        business_warnings.append(message)
        human_decision_items.append("Submitted-together scope includes additional changes; confirm that they are intended.")
    else:
        checks.append({"name": "submitted_together_policy", "status": "passed", "evidence": ["No additional submitted-together scope was detected."]})

    return {
        "checks": checks,
        "business_blockers": unique_strings(business_blockers),
        "business_warnings": unique_strings(business_warnings),
        "next_actions": unique_strings(next_actions),
        "human_decision_items": unique_strings(human_decision_items),
        "reviewers": {
            "counts": reviewer_counts,
            "reviewers": [display_account(account) for account in review_accounts],
            "cc": [display_account(account) for account in reviewer_state_accounts(reviewers, "CC")],
        },
        "labels": {
            "visible": visible_labels,
            "code_review_approved": code_review_approved,
            "verified_visible": verified_visible,
            "verified_approved": verified_approved,
        },
    }


def pre_submit_decision_summary(
    submit_plan: Mapping[str, Any],
    business_assessment: Mapping[str, Any],
) -> tuple[str, str, bool]:
    technical_blockers = [
        string_value(blocker.get("summary")) or string_value(blocker.get("name"))
        for blocker in submit_plan.get("blockers", [])
        if isinstance(blocker, Mapping)
    ]
    business_blockers = [str(item) for item in business_assessment.get("business_blockers", []) if item]
    business_warnings = [str(item) for item in business_assessment.get("business_warnings", []) if item]
    human_items = [str(item) for item in business_assessment.get("human_decision_items", []) if item]
    plan_warnings = [str(item) for item in submit_plan.get("warnings", []) if item]
    needs_human_decision = bool(human_items)

    blockers = unique_strings([*technical_blockers, *business_blockers])
    if blockers:
        summary = "Submit is blocked: " + "; ".join(blockers[:3]) + ("; additional blockers remain." if len(blockers) > 3 else ".")
        return "blocked", summary, needs_human_decision

    warnings = unique_strings([*plan_warnings, *business_warnings])
    if warnings or needs_human_decision:
        summary_parts = warnings[:2]
        if needs_human_decision:
            summary_parts.append("human judgment is still required")
        summary = "Submit requires manual attention: " + "; ".join(summary_parts) + "."
        return "warning", summary, needs_human_decision

    return "pass", "No known technical or workflow blockers were found before submit.", False


def build_review_brief_intent_summary(
    change_summary: Mapping[str, Any],
    overview: Mapping[str, Any],
    files_to_inspect: Sequence[Mapping[str, Any]],
) -> str:
    subject = string_value(change_summary.get("subject")) or string_value(change_summary.get("id")) or "Change"
    files_changed = coerce_int(overview.get("files_changed"))
    top_areas = list(overview.get("top_areas") or [])
    summary = (
        f"{subject} touches {files_changed} files (+{coerce_int(overview.get('lines_inserted'))}"
        f"/-{coerce_int(overview.get('lines_deleted'))})"
    )
    if top_areas:
        summary += f" across {', '.join(str(area) for area in top_areas)}"
    if files_to_inspect:
        summary += "; inspect " + ", ".join(string_value(item.get("file")) for item in files_to_inspect[:2]) + " first"
    unresolved_count = coerce_int(overview.get("unresolved_comment_count"))
    if unresolved_count:
        summary += f"; {unresolved_count} unresolved comment threads remain"
    return summary + "."


def build_review_brief_open_questions(
    change_summary: Mapping[str, Any],
    overview: Mapping[str, Any],
    file_risks: Sequence[Mapping[str, Any]],
) -> List[str]:
    questions: List[str] = []
    unresolved_count = coerce_int(overview.get("unresolved_comment_count"))
    if unresolved_count:
        questions.append(
            f"{unresolved_count} unresolved comment threads exist; confirm whether they still apply to the current patch set."
        )
    if bool(overview.get("test_gap")):
        questions.append("No test file changed with the code; ask for test evidence or a rationale for the gap.")
    if any("security" in (item.get("categories") or []) for item in file_risks):
        questions.append("Security-sensitive paths changed; verify auth, permission, token, and secret handling invariants.")
    if any("config" in (item.get("categories") or []) for item in file_risks):
        questions.append("Build or config files changed; confirm CI, release, or deployment defaults still hold.")
    if any("generated" in (item.get("categories") or []) for item in file_risks):
        questions.append("Generated or vendor-style artifacts changed; confirm the checked-in output matches its source.")
    if bool(change_summary.get("work_in_progress", False)):
        questions.append("The change is still marked WIP; confirm whether detailed review should wait for a later patch set.")
    if bool(change_summary.get("is_private", False)):
        questions.append("The change is private; keep review context within the allowed audience.")
    return questions


def build_review_brief_next_actions(
    change_summary: Mapping[str, Any],
    overview: Mapping[str, Any],
    files_to_inspect: Sequence[Mapping[str, Any]],
) -> List[str]:
    actions: List[str] = []
    if files_to_inspect:
        actions.append(
            "Inspect files in this order: "
            + ", ".join(string_value(item.get("file")) for item in files_to_inspect)
            + "."
        )
    else:
        actions.append("Inspect the changed files in Gerrit before voting.")
    if coerce_int(overview.get("unresolved_comment_count")):
        actions.append("Read unresolved comment threads before adding new feedback.")
    if bool(overview.get("test_gap")):
        actions.append("Ask the owner for test evidence or rationale because no test file changed with the code.")
    if bool(change_summary.get("work_in_progress", False)):
        actions.append("Hold off on voting until the owner marks the change ready for review.")
    if bool(change_summary.get("is_private", False)):
        actions.append("Keep any review notes within the private-change audience.")
    actions.append("This workflow is report-only; publish comments or votes separately after manual inspection.")
    return actions


def review_brief_decision_status(
    change_summary: Mapping[str, Any],
    overview: Mapping[str, Any],
    file_risks: Sequence[Mapping[str, Any]],
) -> str:
    high_risk = any(coerce_int(item.get("risk_score")) >= 5 for item in file_risks)
    if (
        coerce_int(overview.get("unresolved_comment_count"))
        or bool(overview.get("test_gap"))
        or bool(change_summary.get("work_in_progress", False))
        or bool(change_summary.get("is_private", False))
        or high_risk
    ):
        return "warning"
    return "pass"


def handle_review_brief(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    if args.max_diff_files < 1 or args.max_diff_files > MAX_REVIEW_BRIEF_DIFF_LIMIT:
        raise CLIUsageError(
            f"--max-diff-files must be between 1 and {MAX_REVIEW_BRIEF_DIFF_LIMIT}."
        )

    checks: List[Mapping[str, Any]] = [workflow_cli_check()]
    warnings: List[str] = []
    next_actions: List[str] = []
    used_commands: List[str] = []

    references_check, core_references_ok, policy_references_complete, reference_actions = required_reference_check()
    checks.append(references_check)
    next_actions.extend(reference_actions)

    try:
        requested_home, active_gerrit_home, active_gerrit_cli, active_gerrit_home_source = resolve_active_gerrit_dependency(env)
    except WorkflowError as exc:
        target = {
            "workflow_cli": str(script_path()),
            "change": args.change,
            "revision": args.revision,
            "max_diff_files": args.max_diff_files,
        }
        checks.append(
            {
                "name": "active_gerrit_cli",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "review-brief",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=configured_active_gerrit_home(env)[0],
            active_gerrit_home_source=configured_active_gerrit_home(env)[1],
        )

    target = {
        "workflow_cli": str(script_path()),
        "active_gerrit_home": str(active_gerrit_home),
        "active_gerrit_cli": str(active_gerrit_cli),
        "change": args.change,
        "revision": args.revision,
        "max_diff_files": args.max_diff_files,
    }
    checks.append(active_gerrit_path_check(active_gerrit_home, active_gerrit_cli, active_gerrit_home_source))

    try:
        change_result = run_active_gerrit_command(
            args,
            env,
            active_gerrit_cli,
            "get-change",
            ["--change", args.change, "--detail", "full"],
        )
    except WorkflowError as exc:
        checks.append(
            {
                "name": "review_brief_get_change",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "review-brief",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    used_commands.append("get-change")
    change_document = change_result["document"]
    extend_active_gerrit_warnings(warnings, "get-change", change_document, change_result["stderr"])
    if change_result["returncode"] != EXIT_SUCCESS or not change_document.get("ok"):
        failure_message = summarize_active_gerrit_failure(
            "get-change",
            change_document,
            change_result["returncode"],
            change_result["stderr"],
        )
        checks.append(
            {
                "name": "review_brief_get_change",
                "status": "failed",
                "evidence": [failure_message],
                "details": change_document,
            }
        )
        error = active_gerrit_error_payload(
            "get-change",
            change_document,
            change_result["returncode"],
            change_result["stderr"],
            env,
        )
        return failure_report(
            "review-brief",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    try:
        change_detail = extract_change_detail(change_document)
    except WorkflowError as exc:
        checks.append(
            {
                "name": "review_brief_get_change",
                "status": "failed",
                "evidence": [str(exc)],
                "details": change_document,
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "review-brief",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    change_summary = change_detail.get("summary") if isinstance(change_detail.get("summary"), Mapping) else {}
    messages = change_detail.get("messages") if isinstance(change_detail.get("messages"), list) else []
    checks.append(
        {
            "name": "review_brief_get_change",
            "status": "passed",
            "evidence": [
                f"Fetched change detail for {args.change} at patch set {change_summary.get('current_patch_set')}."
            ],
            "details": {
                "invocation": change_result["argv"][2:],
                "message_count": len(messages),
            },
        }
    )

    try:
        files_result = run_active_gerrit_command(
            args,
            env,
            active_gerrit_cli,
            "list-files",
            ["--change", args.change, "--revision", args.revision],
        )
    except WorkflowError as exc:
        checks.append(
            {
                "name": "review_brief_list_files",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "review-brief",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    used_commands.append("list-files")
    files_document = files_result["document"]
    extend_active_gerrit_warnings(warnings, "list-files", files_document, files_result["stderr"])
    if files_result["returncode"] != EXIT_SUCCESS or not files_document.get("ok"):
        failure_message = summarize_active_gerrit_failure(
            "list-files",
            files_document,
            files_result["returncode"],
            files_result["stderr"],
        )
        checks.append(
            {
                "name": "review_brief_list_files",
                "status": "failed",
                "evidence": [failure_message],
                "details": files_document,
            }
        )
        error = active_gerrit_error_payload(
            "list-files",
            files_document,
            files_result["returncode"],
            files_result["stderr"],
            env,
        )
        return failure_report(
            "review-brief",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    try:
        file_listing = extract_file_listing(files_document)
    except WorkflowError as exc:
        checks.append(
            {
                "name": "review_brief_list_files",
                "status": "failed",
                "evidence": [str(exc)],
                "details": files_document,
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "review-brief",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    raw_files = file_listing.get("files") if isinstance(file_listing.get("files"), list) else []
    file_risks = [
        build_review_brief_file_risk(file_entry)
        for file_entry in raw_files
        if isinstance(file_entry, Mapping)
    ]
    selected_files = select_review_brief_files(file_risks, args.max_diff_files)
    checks.append(
        {
            "name": "review_brief_list_files",
            "status": "passed",
            "evidence": [
                f"Fetched {len(file_risks)} file summaries for revision {file_listing.get('revision')}."
            ],
            "details": {
                "invocation": files_result["argv"][2:],
                "selected_files": [item.get("file") for item in selected_files],
            },
        }
    )

    files_to_inspect: List[Dict[str, Any]] = []
    diff_invocations: List[Sequence[str]] = []
    for selected_file in selected_files:
        try:
            diff_result = run_active_gerrit_command(
                args,
                env,
                active_gerrit_cli,
                "get-diff",
                [
                    "--change",
                    args.change,
                    "--revision",
                    args.revision,
                    "--file",
                    string_value(selected_file.get("file")),
                ],
            )
        except WorkflowError as exc:
            checks.append(
                {
                    "name": "review_brief_diffs",
                    "status": "failed",
                    "evidence": [str(exc)],
                    "details": {"file": selected_file.get("file")},
                }
            )
            error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
            return failure_report(
                "review-brief",
                args,
                env,
                error,
                checks=checks,
                next_actions=next_actions,
                warnings=warnings,
                target=target,
                used_active_gerrit_commands=used_commands,
                active_gerrit_home=active_gerrit_home,
                active_gerrit_cli=active_gerrit_cli,
                active_gerrit_home_source=active_gerrit_home_source,
            )

        used_commands.append("get-diff")
        diff_document = diff_result["document"]
        extend_active_gerrit_warnings(warnings, "get-diff", diff_document, diff_result["stderr"])
        if diff_result["returncode"] != EXIT_SUCCESS or not diff_document.get("ok"):
            failure_message = summarize_active_gerrit_failure(
                "get-diff",
                diff_document,
                diff_result["returncode"],
                diff_result["stderr"],
            )
            checks.append(
                {
                    "name": "review_brief_diffs",
                    "status": "failed",
                    "evidence": [failure_message],
                    "details": {
                        "file": selected_file.get("file"),
                        "document": diff_document,
                    },
                }
            )
            error = active_gerrit_error_payload(
                "get-diff",
                diff_document,
                diff_result["returncode"],
                diff_result["stderr"],
                env,
            )
            return failure_report(
                "review-brief",
                args,
                env,
                error,
                checks=checks,
                next_actions=next_actions,
                warnings=warnings,
                target=target,
                used_active_gerrit_commands=used_commands,
                active_gerrit_home=active_gerrit_home,
                active_gerrit_cli=active_gerrit_cli,
                active_gerrit_home_source=active_gerrit_home_source,
            )

        try:
            diff_payload = extract_file_diff(diff_document)
        except WorkflowError as exc:
            checks.append(
                {
                    "name": "review_brief_diffs",
                    "status": "failed",
                    "evidence": [str(exc)],
                    "details": {
                        "file": selected_file.get("file"),
                        "document": diff_document,
                    },
                }
            )
            error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
            return failure_report(
                "review-brief",
                args,
                env,
                error,
                checks=checks,
                next_actions=next_actions,
                warnings=warnings,
                target=target,
                used_active_gerrit_commands=used_commands,
                active_gerrit_home=active_gerrit_home,
                active_gerrit_cli=active_gerrit_cli,
                active_gerrit_home_source=active_gerrit_home_source,
            )

        diff_invocations.append(diff_result["argv"][2:])
        files_to_inspect.append(
            {
                "file": selected_file.get("file"),
                "status": selected_file.get("status"),
                "area": selected_file.get("area"),
                "lines_changed": selected_file.get("lines_changed"),
                "risk_score": selected_file.get("risk_score"),
                "risk_reasons": list(selected_file.get("risk_reasons") or []),
                "categories": list(selected_file.get("categories") or []),
                "suggested_focus": review_focus_for_file(selected_file),
                "diff": summarize_review_brief_diff(diff_payload),
            }
        )

    overview = build_review_brief_overview(change_summary, change_detail, file_listing, file_risks)
    checks.append(
        {
            "name": "review_brief_diffs",
            "status": "passed",
            "evidence": [
                f"Fetched diff previews for {len(files_to_inspect)} selected files."
            ],
            "details": {
                "requested_files": [item.get("file") for item in selected_files],
                "invocations": diff_invocations,
            },
        }
    )

    intent_summary = build_review_brief_intent_summary(change_summary, overview, files_to_inspect)
    open_questions = build_review_brief_open_questions(change_summary, overview, file_risks)
    next_actions.extend(build_review_brief_next_actions(change_summary, overview, files_to_inspect))
    decision_status = review_brief_decision_status(change_summary, overview, file_risks)

    ok, decision_status, intent_summary, needs_human_decision = apply_reference_policy_result(
        decision_status,
        intent_summary,
        core_references_ok=core_references_ok,
        policy_references_complete=policy_references_complete,
        blocked_summary="Workflow references are missing, so the review brief may be incomplete.",
        human_gap_summary="local policy references are incomplete, so manual policy confirmation is required.",
    )

    owner = change_summary.get("owner") if isinstance(change_summary.get("owner"), Mapping) else {}
    return workflow_report(
        "review-brief",
        ok,
        target,
        decision_status,
        intent_summary,
        checks,
        used_commands,
        next_actions,
        args,
        needs_human_decision=needs_human_decision,
        warnings=warnings,
        active_gerrit_home=active_gerrit_home,
        active_gerrit_cli=active_gerrit_cli,
        active_gerrit_home_source=active_gerrit_home_source,
        extra={
            "brief": {
                "intent_summary": intent_summary,
                "change": {
                    "id": change_summary.get("id"),
                    "number": change_summary.get("number"),
                    "project": change_summary.get("project"),
                    "branch": change_summary.get("branch"),
                    "subject": change_summary.get("subject"),
                    "status": change_summary.get("status"),
                    "owner": dict(owner),
                    "updated": change_summary.get("updated"),
                    "current_patch_set": change_summary.get("current_patch_set"),
                    "topic": change_summary.get("topic"),
                    "hashtags": list(change_summary.get("hashtags") or []),
                    "work_in_progress": bool(change_summary.get("work_in_progress", False)),
                    "is_private": bool(change_summary.get("is_private", False)),
                },
                "changed_file_overview": overview,
                "risk_areas": build_review_brief_risk_areas(file_risks),
                "files_to_inspect": files_to_inspect,
                "open_questions": open_questions,
                "review_order": [item.get("file") for item in files_to_inspect],
            }
        },
    )


def handle_pre_submit_check(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    checks: List[Mapping[str, Any]] = [workflow_cli_check()]
    warnings: List[str] = []
    next_actions: List[str] = []
    used_commands: List[str] = []

    references_check, core_references_ok, policy_references_complete, reference_actions = required_reference_check()
    checks.append(references_check)
    next_actions.extend(reference_actions)

    try:
        requested_home, active_gerrit_home, active_gerrit_cli, active_gerrit_home_source = resolve_active_gerrit_dependency(env)
    except WorkflowError as exc:
        target = {
            "workflow_cli": str(script_path()),
            "change": args.change,
        }
        checks.append(
            {
                "name": "active_gerrit_cli",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=configured_active_gerrit_home(env)[0],
            active_gerrit_home_source=configured_active_gerrit_home(env)[1],
        )

    target = {
        "workflow_cli": str(script_path()),
        "active_gerrit_home": str(active_gerrit_home),
        "active_gerrit_cli": str(active_gerrit_cli),
        "change": args.change,
    }
    checks.append(active_gerrit_path_check(active_gerrit_home, active_gerrit_cli, active_gerrit_home_source))

    try:
        submit_result = run_active_gerrit_command(
            args,
            env,
            active_gerrit_cli,
            "submit",
            ["--change", args.change, "--dry-run"],
        )
    except WorkflowError as exc:
        checks.append(
            {
                "name": "base_submit_dry_run",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    used_commands.append("submit")
    submit_document = submit_result["document"]
    extend_active_gerrit_warnings(warnings, "submit", submit_document, submit_result["stderr"])
    if submit_result["returncode"] != EXIT_SUCCESS or not submit_document.get("ok"):
        failure_message = summarize_active_gerrit_failure(
            "submit",
            submit_document,
            submit_result["returncode"],
            submit_result["stderr"],
        )
        checks.append(
            {
                "name": "base_submit_dry_run",
                "status": "failed",
                "evidence": [failure_message],
                "details": submit_document,
            }
        )
        error = active_gerrit_error_payload(
            "submit",
            submit_document,
            submit_result["returncode"],
            submit_result["stderr"],
            env,
        )
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    try:
        submit_plan = extract_submit_plan(submit_document)
    except WorkflowError as exc:
        checks.append(
            {
                "name": "base_submit_dry_run",
                "status": "failed",
                "evidence": [str(exc)],
                "details": submit_document,
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    warnings.extend(f"submit --dry-run: {warning}" for warning in submit_plan.get("warnings", []) if warning)
    next_actions.extend(str(action) for action in submit_plan.get("next_actions", []) if action)
    checks.append(
        {
            "name": "base_submit_dry_run",
            "status": "passed" if bool(submit_plan.get("ready")) else "failed",
            "evidence": [string_value(submit_plan.get("reason")) or "Collected submit dry-run plan."],
            "details": {
                "invocation": submit_result["argv"][2:],
                "ready": bool(submit_plan.get("ready")),
                "blocker_count": len([item for item in submit_plan.get("blockers", []) if isinstance(item, Mapping)]),
                "warning_count": len([item for item in submit_plan.get("warnings", []) if item]),
            },
        }
    )
    checks.extend(dict(item) for item in submit_plan.get("checks", []) if isinstance(item, Mapping))

    try:
        change_result = run_active_gerrit_command(
            args,
            env,
            active_gerrit_cli,
            "get-change",
            ["--change", args.change, "--detail", "detail"],
        )
    except WorkflowError as exc:
        checks.append(
            {
                "name": "pre_submit_get_change",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    used_commands.append("get-change")
    change_document = change_result["document"]
    extend_active_gerrit_warnings(warnings, "get-change", change_document, change_result["stderr"])
    if change_result["returncode"] != EXIT_SUCCESS or not change_document.get("ok"):
        failure_message = summarize_active_gerrit_failure(
            "get-change",
            change_document,
            change_result["returncode"],
            change_result["stderr"],
        )
        checks.append(
            {
                "name": "pre_submit_get_change",
                "status": "failed",
                "evidence": [failure_message],
                "details": change_document,
            }
        )
        error = active_gerrit_error_payload(
            "get-change",
            change_document,
            change_result["returncode"],
            change_result["stderr"],
            env,
        )
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    try:
        change_detail = extract_change_detail(change_document)
    except WorkflowError as exc:
        checks.append(
            {
                "name": "pre_submit_get_change",
                "status": "failed",
                "evidence": [str(exc)],
                "details": change_document,
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    change_summary = change_detail.get("summary") if isinstance(change_detail.get("summary"), Mapping) else {}
    target.update(
        {
            "project": change_summary.get("project") or submit_plan.get("project"),
            "branch": change_summary.get("branch") or submit_plan.get("branch"),
        }
    )
    checks.append(
        {
            "name": "pre_submit_get_change",
            "status": "passed",
            "evidence": [f"Fetched change detail for {args.change} at patch set {change_summary.get('current_patch_set')}."] ,
            "details": {"invocation": change_result["argv"][2:]},
        }
    )

    try:
        files_result = run_active_gerrit_command(
            args,
            env,
            active_gerrit_cli,
            "list-files",
            ["--change", args.change, "--revision", "current"],
        )
    except WorkflowError as exc:
        checks.append(
            {
                "name": "pre_submit_list_files",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    used_commands.append("list-files")
    files_document = files_result["document"]
    extend_active_gerrit_warnings(warnings, "list-files", files_document, files_result["stderr"])
    if files_result["returncode"] != EXIT_SUCCESS or not files_document.get("ok"):
        failure_message = summarize_active_gerrit_failure(
            "list-files",
            files_document,
            files_result["returncode"],
            files_result["stderr"],
        )
        checks.append(
            {
                "name": "pre_submit_list_files",
                "status": "failed",
                "evidence": [failure_message],
                "details": files_document,
            }
        )
        error = active_gerrit_error_payload(
            "list-files",
            files_document,
            files_result["returncode"],
            files_result["stderr"],
            env,
        )
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    try:
        file_listing = extract_file_listing(files_document)
    except WorkflowError as exc:
        checks.append(
            {
                "name": "pre_submit_list_files",
                "status": "failed",
                "evidence": [str(exc)],
                "details": files_document,
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "pre-submit-check",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    raw_files = file_listing.get("files") if isinstance(file_listing.get("files"), list) else []
    file_risks = [
        build_review_brief_file_risk(file_entry)
        for file_entry in raw_files
        if isinstance(file_entry, Mapping)
    ]
    overview = build_review_brief_overview(change_summary, change_detail, file_listing, file_risks)
    checks.append(
        {
            "name": "pre_submit_list_files",
            "status": "passed",
            "evidence": [f"Fetched {len(file_risks)} file summaries for pre-submit risk checks."],
            "details": {"invocation": files_result["argv"][2:]},
        }
    )

    business_assessment = build_pre_submit_business_assessment(change_summary, change_detail, submit_plan, overview, file_risks)
    checks.extend(dict(item) for item in business_assessment["checks"])
    warnings.extend(str(item) for item in business_assessment["business_warnings"])
    next_actions.extend(str(item) for item in business_assessment["next_actions"])
    next_actions.append("This workflow never executes submit; run active-gerrit submit --yes only after manual confirmation.")

    decision_status, decision_summary, needs_human_decision = pre_submit_decision_summary(submit_plan, business_assessment)
    ok, decision_status, decision_summary, needs_human_decision = apply_reference_policy_result(
        decision_status,
        decision_summary,
        core_references_ok=core_references_ok,
        policy_references_complete=policy_references_complete,
        blocked_summary="Workflow references are missing, so the pre-submit report may be incomplete.",
        human_gap_summary="local policy references are incomplete, so manual submit approval is still required.",
        base_needs_human_decision=needs_human_decision,
    )

    submit_plan_snapshot = {
        "ready": bool(submit_plan.get("ready")),
        "reason": submit_plan.get("reason"),
        "dry_run": bool(submit_plan.get("dry_run")),
        "current_status": submit_plan.get("current_status"),
        "current_revision": submit_plan.get("current_revision"),
        "revision_sha": submit_plan.get("revision_sha"),
        "patch_set": submit_plan.get("patch_set"),
        "submittable": submit_plan.get("submittable"),
        "submit_requirements": dict(submit_plan.get("submit_requirements") or {}),
        "mergeable": dict(submit_plan.get("mergeable") or {}),
        "submitted_together": dict(submit_plan.get("submitted_together") or {}),
        "blockers": [dict(item) for item in submit_plan.get("blockers", []) if isinstance(item, Mapping)],
        "warnings": [str(item) for item in submit_plan.get("warnings", []) if item],
        "next_actions": [str(item) for item in submit_plan.get("next_actions", []) if item],
        "submit_action": dict(submit_plan.get("submit_action") or {}),
        "planned_request": dict(submit_plan.get("planned_request") or {}),
    }

    return workflow_report(
        "pre-submit-check",
        ok,
        target,
        decision_status,
        decision_summary,
        checks,
        used_commands,
        unique_strings(next_actions),
        args,
        needs_human_decision=needs_human_decision,
        warnings=unique_strings(warnings),
        active_gerrit_home=active_gerrit_home,
        active_gerrit_cli=active_gerrit_cli,
        active_gerrit_home_source=active_gerrit_home_source,
        extra={
            "pre_submit": {
                "base_submit_plan": submit_plan_snapshot,
                "change": build_pre_submit_change_snapshot(change_summary),
                "reviewers": dict(business_assessment.get("reviewers") or {}),
                "labels": dict(business_assessment.get("labels") or {}),
                "changed_file_overview": overview,
                "risk_areas": build_review_brief_risk_areas(file_risks),
                "business_blockers": list(business_assessment.get("business_blockers") or []),
                "business_warnings": list(business_assessment.get("business_warnings") or []),
                "human_decision_items": list(business_assessment.get("human_decision_items") or []),
            }
        },
    )


def handle_my_review_queue(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    if args.limit < 1:
        raise CLIUsageError("--limit must be at least 1.")

    checks: List[Mapping[str, Any]] = [workflow_cli_check()]
    warnings: List[str] = []
    next_actions: List[str] = []
    used_commands: List[str] = []

    references_check, core_references_ok, policy_references_complete, reference_actions = required_reference_check()
    checks.append(references_check)
    next_actions.extend(reference_actions)

    try:
        requested_home, active_gerrit_home, active_gerrit_cli, active_gerrit_home_source = resolve_active_gerrit_dependency(env)
    except WorkflowError as exc:
        target = {
            "workflow_cli": str(script_path()),
            "preset": "my_open_reviews",
            "limit": args.limit,
        }
        checks.append(
            {
                "name": "active_gerrit_cli",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "my-review-queue",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=configured_active_gerrit_home(env)[0],
            active_gerrit_home_source=configured_active_gerrit_home(env)[1],
        )

    target = {
        "workflow_cli": str(script_path()),
        "active_gerrit_home": str(active_gerrit_home),
        "active_gerrit_cli": str(active_gerrit_cli),
        "preset": "my_open_reviews",
        "limit": args.limit,
    }
    checks.append(active_gerrit_path_check(active_gerrit_home, active_gerrit_cli, active_gerrit_home_source))

    try:
        result = run_active_gerrit_command(
            args,
            env,
            active_gerrit_cli,
            "query-preset",
            [
                "my_open_reviews",
                "--limit",
                str(args.limit),
                "--option",
                REVIEWED_QUERY_OPTION,
            ],
        )
    except WorkflowError as exc:
        checks.append(
            {
                "name": "review_queue_query",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "my-review-queue",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    used_commands.append("query-preset")
    base_document = result["document"]
    extend_active_gerrit_warnings(warnings, "query-preset", base_document, result["stderr"])

    if result["returncode"] != EXIT_SUCCESS or not base_document.get("ok"):
        failure_message = summarize_active_gerrit_failure("query-preset", base_document, result["returncode"], result["stderr"])
        checks.append(
            {
                "name": "review_queue_query",
                "status": "failed",
                "evidence": [failure_message],
                "details": base_document,
            }
        )
        error = active_gerrit_error_payload("query-preset", base_document, result["returncode"], result["stderr"], env)
        return failure_report(
            "my-review-queue",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    try:
        changes = sort_review_queue_changes(extract_change_summaries(base_document))
    except WorkflowError as exc:
        checks.append(
            {
                "name": "review_queue_query",
                "status": "failed",
                "evidence": [str(exc)],
                "details": base_document,
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "my-review-queue",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            warnings=warnings,
            target=target,
            used_active_gerrit_commands=used_commands,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    queue_items = [build_review_queue_item(change) for change in changes]
    queue_summary = build_review_queue_summary(queue_items)
    decision_status, decision_summary = review_queue_decision(queue_summary)
    next_actions.extend(build_review_queue_next_actions(queue_summary))
    checks.append(
        {
            "name": "review_queue_query",
            "status": "passed",
            "evidence": [f"Fetched {len(queue_items)} open review changes from query-preset my_open_reviews."],
            "details": {
                "invocation": result["argv"][2:],
                "result_count": len(queue_items),
            },
        }
    )
    triage_status = "warning" if decision_status == "warning" else "passed"
    checks.append(
        {
            "name": "review_queue_triage",
            "status": triage_status,
            "evidence": [
                f"needs_my_response_changes={queue_summary['needs_my_response_changes']}",
                f"changes_with_unresolved_comments={queue_summary['changes_with_unresolved_comments']}",
                f"work_in_progress_changes={queue_summary['work_in_progress_changes']}",
                f"private_changes={queue_summary['private_changes']}",
                f"release_branch_changes={queue_summary['release_branch_changes']}",
            ],
        }
    )

    ok, decision_status, decision_summary, needs_human_decision = apply_reference_policy_result(
        decision_status,
        decision_summary,
        core_references_ok=core_references_ok,
        policy_references_complete=policy_references_complete,
        blocked_summary="Workflow references are missing, so the review queue may be incomplete.",
        human_gap_summary="local policy references are incomplete, so queue prioritization still needs human confirmation.",
    )
    return workflow_report(
        "my-review-queue",
        ok,
        target,
        decision_status,
        decision_summary,
        checks,
        used_commands,
        next_actions,
        args,
        needs_human_decision=needs_human_decision,
        warnings=warnings,
        active_gerrit_home=active_gerrit_home,
        active_gerrit_cli=active_gerrit_cli,
        active_gerrit_home_source=active_gerrit_home_source,
        extra={
            "queue": {
                "sort_order": "updated_oldest_first",
                "summary": queue_summary,
                "changes": queue_items,
            }
        },
    )


def failure_report(
    workflow: str,
    args: argparse.Namespace,
    env: Mapping[str, str],
    error: Mapping[str, Any],
    *,
    checks: Optional[Sequence[Mapping[str, Any]]] = None,
    next_actions: Optional[Sequence[str]] = None,
    warnings: Optional[Sequence[str]] = None,
    target: Optional[Mapping[str, Any]] = None,
    used_active_gerrit_commands: Optional[Sequence[str]] = None,
    active_gerrit_home: Optional[Path] = None,
    active_gerrit_cli: Optional[Path] = None,
    active_gerrit_home_source: Optional[str] = None,
) -> Dict[str, Any]:
    summary = str(error.get("message") or "Workflow execution failed.")
    actions = list(next_actions or [])
    hint = error.get("hint")
    if isinstance(hint, str) and hint and hint not in actions:
        actions.append(hint)
    return workflow_report(
        workflow,
        False,
        target or {},
        "blocked",
        summary,
        checks or [],
        used_active_gerrit_commands or [],
        actions,
        args,
        warnings=warnings,
        error=error,
        active_gerrit_home=active_gerrit_home,
        active_gerrit_cli=active_gerrit_cli,
        active_gerrit_home_source=active_gerrit_home_source,
    )


def handle_doctor(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    checks: List[Mapping[str, Any]] = [workflow_cli_check()]
    warnings: List[str] = []
    next_actions: List[str] = []
    used_commands: List[str] = []

    references_check, core_references_ok, policy_references_complete, reference_actions = required_reference_check()
    checks.append(references_check)
    next_actions.extend(reference_actions)

    requested_home, active_gerrit_home_source = configured_active_gerrit_home(env)
    target: Dict[str, Any] = {
        "workflow_cli": str(script_path()),
        "active_gerrit_home": str(requested_home),
    }

    try:
        _, active_gerrit_home, active_gerrit_cli, _ = resolve_active_gerrit_dependency(env)
    except WorkflowError as exc:
        checks.append(
            {
                "name": "active_gerrit_cli",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "doctor",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=requested_home,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    target["active_gerrit_home"] = str(active_gerrit_home)
    target["active_gerrit_cli"] = str(active_gerrit_cli)
    checks.append(active_gerrit_path_check(active_gerrit_home, active_gerrit_cli, active_gerrit_home_source))

    try:
        result = run_active_gerrit_command(args, env, active_gerrit_cli, "doctor")
    except WorkflowError as exc:
        checks.append(
            {
                "name": "active_gerrit_doctor",
                "status": "failed",
                "evidence": [str(exc)],
            }
        )
        error = error_details(exc.error_type, exc, env, hint=exc.hint, status=exc.status)
        return failure_report(
            "doctor",
            args,
            env,
            error,
            checks=checks,
            next_actions=next_actions,
            target=target,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    used_commands.append("doctor")
    base_document = result["document"]
    extend_active_gerrit_warnings(warnings, "doctor", base_document, result["stderr"])

    if result["returncode"] == EXIT_SUCCESS and base_document.get("ok") and core_references_ok:
        checks.append(
            {
                "name": "active_gerrit_doctor",
                "status": "passed",
                "evidence": ["active-gerrit doctor completed successfully."],
                "details": base_document,
            }
        )
        ok, decision_status, decision_summary, needs_human_decision = apply_reference_policy_result(
            "pass",
            "Workflow layer can reach active-gerrit doctor and required references are present.",
            core_references_ok=core_references_ok,
            policy_references_complete=policy_references_complete,
            blocked_summary="Workflow layer can reach active-gerrit doctor, but required workflow references are missing.",
            human_gap_summary="local policy references are incomplete, so some workflow decisions will still require human confirmation.",
        )
        return workflow_report(
            "doctor",
            ok,
            target,
            decision_status,
            decision_summary,
            checks,
            used_commands,
            next_actions,
            args,
            needs_human_decision=needs_human_decision,
            warnings=warnings,
            active_gerrit_home=active_gerrit_home,
            active_gerrit_cli=active_gerrit_cli,
            active_gerrit_home_source=active_gerrit_home_source,
        )

    failure_message = summarize_active_gerrit_failure("doctor", base_document, result["returncode"], result["stderr"])
    checks.append(
        {
            "name": "active_gerrit_doctor",
            "status": "failed",
            "evidence": [failure_message],
            "details": base_document,
        }
    )
    error = active_gerrit_error_payload("doctor", base_document, result["returncode"], result["stderr"], env)
    if not core_references_ok and "Restore the required workflow reference files before using workflow commands." not in next_actions:
        next_actions.append("Restore the required workflow reference files before using workflow commands.")
    return failure_report(
        "doctor",
        args,
        env,
        error,
        checks=checks,
        next_actions=next_actions,
        warnings=warnings,
        target=target,
        used_active_gerrit_commands=used_commands,
        active_gerrit_home=active_gerrit_home,
        active_gerrit_cli=active_gerrit_cli,
        active_gerrit_home_source=active_gerrit_home_source,
    )


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="workflow_cli.py",
        description="active-gerrit-workflow command line tools",
    )
    parser.add_argument("--trace", help="Pass through Gerrit trace metadata to active-gerrit.")
    parser.add_argument("--deadline", help="Pass through Gerrit deadline metadata to active-gerrit.")
    parser.add_argument("--no-cache", action="store_true", help="Pass through cache bypass to active-gerrit.")
    parser.add_argument("--refresh", action="store_true", help="Pass through cache refresh to active-gerrit.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Check workflow references and the active-gerrit dependency.")
    doctor.set_defaults(handler=handle_doctor)
    my_review_queue = subparsers.add_parser(
        "my-review-queue",
        help="Summarize open changes waiting for the current user's review.",
    )
    my_review_queue.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_REVIEW_QUEUE_LIMIT,
        help="Maximum number of open review changes to inspect.",
    )
    my_review_queue.set_defaults(handler=handle_my_review_queue)
    pre_submit_check = subparsers.add_parser(
        "pre-submit-check",
        help="Run technical and workflow-level submit readiness checks without submitting.",
    )
    pre_submit_check.add_argument("--change", required=True, help="Change identifier accepted by active-gerrit.")
    pre_submit_check.set_defaults(handler=handle_pre_submit_check)
    review_brief = subparsers.add_parser(
        "review-brief",
        help="Summarize one change before manual review.",
    )
    review_brief.add_argument("--change", required=True, help="Change identifier accepted by active-gerrit.")
    review_brief.add_argument(
        "--revision",
        default="current",
        help="Revision to inspect for list-files and get-diff. Defaults to current.",
    )
    review_brief.add_argument(
        "--max-diff-files",
        type=int,
        default=DEFAULT_REVIEW_BRIEF_DIFF_LIMIT,
        help="Maximum number of high-risk files to fetch diff previews for.",
    )
    review_brief.set_defaults(handler=handle_review_brief)
    return parser


def print_json(document: Mapping[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


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
        document = failure_report(
            command_name(args),
            args,
            actual_env,
            error_details(
                "ValidationError",
                exc,
                actual_env,
                hint="Run with --help to inspect available workflow commands and options.",
            ),
        )
        print_json(document)
        return EXIT_USAGE
    except WorkflowError as exc:
        args = fallback_args(args)
        document = failure_report(
            command_name(args),
            args,
            actual_env,
            error_details(exc.error_type, exc, actual_env, hint=exc.hint, status=exc.status),
        )
        print_json(document)
        return EXIT_CONFIG if exc.error_type == "WorkflowConfigError" else EXIT_FAILURE
    except Exception as exc:  # pragma: no cover - last-resort safety net.
        args = fallback_args(args)
        document = failure_report(
            command_name(args),
            args,
            actual_env,
            error_details("UnexpectedError", exc, actual_env),
        )
        print_json(document)
        return EXIT_FAILURE


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()