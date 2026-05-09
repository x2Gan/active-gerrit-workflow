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
        active_gerrit_home, active_gerrit_cli = resolve_active_gerrit_cli(requested_home)
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
    base_warnings = base_document.get("warnings") if isinstance(base_document.get("warnings"), list) else []
    for warning in base_warnings:
        warnings.append(f"active-gerrit doctor: {warning}")
    if result["stderr"]:
        warnings.append(f"active-gerrit doctor stderr: {result['stderr']}")

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

    failure_message = summarize_active_gerrit_failure(base_document, result["returncode"], result["stderr"])
    checks.append(
        {
            "name": "active_gerrit_doctor",
            "status": "failed",
            "evidence": [failure_message],
            "details": base_document,
        }
    )
    error_payload = base_document.get("error") if isinstance(base_document.get("error"), Mapping) else {}
    error = error_details(
        str(error_payload.get("type") or "WorkflowDependencyError"),
        error_payload.get("message") or failure_message,
        env,
        hint=error_payload.get("hint") if isinstance(error_payload.get("hint"), str) else None,
        status=error_payload.get("status") if isinstance(error_payload.get("status"), int) else None,
    )
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