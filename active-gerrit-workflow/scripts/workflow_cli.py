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
REQUIRED_REFERENCE_FILES = (
    "business-workflows.md",
    "review-policies.md",
)
SECRET_ENV_KEYS = (
    "GERRIT_HTTP_PASSWORD",
    "GERRIT_BEARER_TOKEN",
    "GERRIT_ACCESS_TOKEN",
    "GERRIT_COOKIE",
    "GERRIT_XSRF_TOKEN",
)
DEFAULT_REVIEW_QUEUE_LIMIT = 25
REVIEWED_QUERY_OPTION = "REVIEWED"
RELEASE_BRANCH_MARKERS = (
    "release",
    "stable",
    "hotfix",
)


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
            "needs_human_decision": False,
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


def required_reference_check() -> tuple[Dict[str, Any], bool, List[str]]:
    references_dir = workflow_home() / "references"
    missing = []
    found = []
    for name in REQUIRED_REFERENCE_FILES:
        path = references_dir / name
        if path.is_file():
            found.append(str(path))
        else:
            missing.append(str(path))
    if missing:
        return (
            {
                "name": "workflow_references",
                "status": "failed",
                "evidence": [
                    "Missing required workflow reference files.",
                    *missing,
                ],
            },
            False,
            ["Restore the required workflow reference files before using workflow commands."],
        )
    return (
        {
            "name": "workflow_references",
            "status": "passed",
            "evidence": [
                "Required workflow references are present.",
                *found,
            ],
        },
        True,
        [],
    )


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


def handle_my_review_queue(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    if args.limit < 1:
        raise CLIUsageError("--limit must be at least 1.")

    checks: List[Mapping[str, Any]] = [workflow_cli_check()]
    warnings: List[str] = []
    next_actions: List[str] = []
    used_commands: List[str] = []

    references_check, references_ok, reference_actions = required_reference_check()
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

    ok = references_ok
    if not references_ok:
        decision_status = "blocked"
        decision_summary = "Workflow references are missing, so the review queue may be incomplete."
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

    references_check, references_ok, reference_actions = required_reference_check()
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

    if result["returncode"] == EXIT_SUCCESS and base_document.get("ok") and references_ok:
        checks.append(
            {
                "name": "active_gerrit_doctor",
                "status": "passed",
                "evidence": ["active-gerrit doctor completed successfully."],
                "details": base_document,
            }
        )
        return workflow_report(
            "doctor",
            True,
            target,
            "pass",
            "Workflow layer can reach active-gerrit doctor and required references are present.",
            checks,
            used_commands,
            next_actions,
            args,
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
    if not references_ok and "Restore the required workflow reference files before using workflow commands." not in next_actions:
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