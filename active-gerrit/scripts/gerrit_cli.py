#!/usr/bin/env python3
"""CLI entry point for active-gerrit."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from gerrit_client import (
    GerritClient,
    GerritClientError,
    GerritConfig,
    GerritConfigError,
    GerritHTTPError,
    GerritParseError,
    GerritTransportError,
    decode_response_body,
    redact_text,
)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

SOURCE = "gerrit"
DEFAULT_CHANGE_QUERY_OPTIONS = (
    "CURRENT_REVISION",
    "DETAILED_ACCOUNTS",
    "LABELS",
    "SUBMIT_REQUIREMENTS",
)

QUERY_PRESETS = {
    "my_open_reviews": "reviewer:self -owner:self status:open",
    "my_owned_open": "owner:self status:open",
    "project_open": "project:{project} status:open",
}


class CLIUsageError(Exception):
    """Argument or command usage error."""


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
    for key in (
        "GERRIT_HTTP_PASSWORD",
        "GERRIT_BEARER_TOKEN",
        "GERRIT_ACCESS_TOKEN",
        "GERRIT_COOKIE",
        "GERRIT_XSRF_TOKEN",
    ):
        value = env.get(key)
        if value:
            yield value


def redact_message(message: object, env: Mapping[str, str]) -> str:
    return redact_text(str(message), secrets=secret_values(env))


def base_meta(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "fetched_at": utc_now_iso(),
        "cache": "bypass" if getattr(args, "no_cache", False) else "not_used",
    }
    base_url = env.get("GERRIT_BASE_URL")
    if base_url:
        meta["gerrit_base_url"] = redact_message(base_url, env)
    trace = getattr(args, "trace", None)
    if trace:
        meta["trace"] = trace
    deadline = getattr(args, "deadline", None)
    if deadline:
        meta["deadline"] = deadline
    return meta


def success_envelope(
    command: str,
    data: Any,
    args: argparse.Namespace,
    env: Mapping[str, str],
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "source": SOURCE,
        "data": data,
        "warnings": list(warnings or []),
        "meta": base_meta(args, env),
    }


def error_envelope(
    command: str,
    error_type: str,
    message: object,
    args: argparse.Namespace,
    env: Mapping[str, str],
    status: Optional[int] = None,
    hint: Optional[str] = None,
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    error: Dict[str, Any] = {
        "type": error_type,
        "message": redact_message(message, env),
    }
    if status is not None:
        error["status"] = status
    if hint:
        error["hint"] = hint
    return {
        "ok": False,
        "command": command,
        "source": SOURCE,
        "data": None,
        "warnings": list(warnings or []),
        "error": error,
        "meta": base_meta(args, env),
    }


def http_error_type(status: int) -> str:
    if status == 401:
        return "GerritAuthError"
    if status == 403:
        return "GerritPermissionError"
    if status == 404:
        return "GerritNotFound"
    if status == 409:
        return "GerritConflict"
    if status == 412:
        return "GerritPreconditionFailed"
    return "GerritHTTPError"


def http_error_hint(status: int) -> str:
    if status == 401:
        return "Check GERRIT_USERNAME and GERRIT_HTTP_PASSWORD."
    if status == 403:
        return "Check Gerrit project permission or capability for this operation."
    if status == 404:
        return "The resource may not exist or may be hidden by permissions."
    if status == 409:
        return "Refresh change state and check Gerrit status requirements."
    if status == 412:
        return "Refresh the resource and verify required preconditions."
    return "Check Gerrit response details and request arguments."


def command_name(args: argparse.Namespace) -> str:
    return getattr(args, "command", None) or "unknown"


def fallback_args(args: Optional[argparse.Namespace]) -> argparse.Namespace:
    return args if args is not None else argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)


def handle_ping(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    warnings = []
    if args.trace:
        warnings.append("--trace is accepted by the CLI and will be wired into Gerrit requests by later commands.")
    if args.deadline:
        warnings.append("--deadline is accepted by the CLI and will be wired into Gerrit requests by later commands.")
    if args.no_cache:
        warnings.append("--no-cache is accepted; M1-T03 does not read from cache.")
    return success_envelope(
        "ping",
        {
            "ready": True,
            "cli": "active-gerrit",
            "reserved_options": {
                "trace": args.trace,
                "deadline": args.deadline,
                "no_cache": args.no_cache,
            },
        },
        args,
        env,
        warnings=warnings,
    )


def handle_version(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    response = client.version()
    return success_envelope(
        "version",
        {
            "version": response.data,
            "status": response.status,
        },
        args,
        env,
    )


def command_check(name: str, required: bool, version_args: Sequence[str] = ("--version",)) -> Dict[str, Any]:
    path = shutil.which(name)
    if not path:
        result = {
            "ok": False,
            "required": required,
            "hint": f"Install {name} and make sure it is available on PATH.",
        }
        return result

    result: Dict[str, Any] = {"ok": True, "required": required, "path": path}
    try:
        completed = subprocess.run(
            [path, *version_args],
            input="",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        output = (completed.stdout or completed.stderr).strip().splitlines()
        if output:
            result["version"] = output[0]
        if completed.returncode != 0 and name == "sed":
            result["version"] = "sed available"
    except Exception as exc:
        result["ok"] = False
        result["message"] = str(exc)
        result["hint"] = f"{name} exists but could not be executed."
    return result


def python_check() -> Dict[str, Any]:
    ok = sys.version_info >= (3, 9)
    return {
        "ok": ok,
        "required": True,
        "version": platform.python_version(),
        "executable": sys.executable,
        "hint": None if ok else "Use Python 3.9 or newer.",
    }


def environment_checks(env: Mapping[str, str]) -> Dict[str, Any]:
    auth_type = (env.get("GERRIT_AUTH_TYPE") or "basic").strip().lower()
    checks: Dict[str, Any] = {
        "GERRIT_BASE_URL": {
            "ok": bool(env.get("GERRIT_BASE_URL")),
            "required": True,
            "value": redact_message(env.get("GERRIT_BASE_URL", ""), env) if env.get("GERRIT_BASE_URL") else None,
            "hint": None if env.get("GERRIT_BASE_URL") else "Set GERRIT_BASE_URL to your Gerrit Web root URL.",
        },
        "GERRIT_AUTH_TYPE": {
            "ok": True,
            "required": False,
            "value": auth_type,
        },
        "GERRIT_USERNAME": {
            "ok": auth_type != "basic" or bool(env.get("GERRIT_USERNAME")),
            "required": auth_type == "basic",
            "hint": None if auth_type != "basic" or env.get("GERRIT_USERNAME") else "Set GERRIT_USERNAME.",
        },
        "GERRIT_HTTP_PASSWORD": {
            "ok": auth_type != "basic" or bool(env.get("GERRIT_HTTP_PASSWORD")),
            "required": auth_type == "basic",
            "redacted": bool(env.get("GERRIT_HTTP_PASSWORD")),
            "hint": None
            if auth_type != "basic" or env.get("GERRIT_HTTP_PASSWORD")
            else "Set GERRIT_HTTP_PASSWORD to the Gerrit UI generated HTTP password.",
        },
    }

    try:
        GerritConfig.from_env(env)
        checks["GERRIT_BASE_URL"]["format_ok"] = checks["GERRIT_BASE_URL"]["ok"]
    except GerritConfigError as exc:
        checks["GERRIT_BASE_URL"]["format_ok"] = False
        checks["GERRIT_BASE_URL"]["ok"] = False
        checks["GERRIT_BASE_URL"]["message"] = redact_message(exc, env)
    return checks


def xssi_check() -> Dict[str, Any]:
    try:
        _text, data = decode_response_body(b")]}'\n{\"ok\": true}", "application/json; charset=UTF-8")
        return {"ok": data == {"ok": True}, "required": True}
    except GerritClientError as exc:
        return {"ok": False, "required": True, "message": str(exc)}


def cache_check(env: Mapping[str, str]) -> Dict[str, Any]:
    cache_dir = Path(env.get("GERRIT_CACHE_DIR") or ".cache/gerrit")
    probe = cache_dir / ".doctor-write-test"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True, "required": False, "path": str(cache_dir)}
    except Exception as exc:
        return {
            "ok": False,
            "required": False,
            "path": str(cache_dir),
            "message": redact_message(exc, env),
            "hint": "Cache will be disabled until this directory is writable.",
        }


def normalize_account(data: Any) -> Dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    return {
        "_account_id": data.get("_account_id"),
        "account_id": data.get("_account_id"),
        "username": data.get("username"),
        "email": data.get("email"),
        "name": data.get("name"),
    }


def current_patch_set(change: Mapping[str, Any]) -> Optional[Any]:
    current_revision = change.get("current_revision")
    revisions = change.get("revisions")
    if not current_revision or not isinstance(revisions, Mapping):
        return None
    revision = revisions.get(current_revision)
    if not isinstance(revision, Mapping):
        return None
    return revision.get("_number")


def preferred_change_id(change: Mapping[str, Any]) -> Optional[str]:
    project = change.get("project")
    number = change.get("_number")
    if project is None or number is None:
        return None
    return f"{project}~{number}"


def normalize_change_summary(change: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": preferred_change_id(change),
        "triplet_id": change.get("id"),
        "number": change.get("_number"),
        "project": change.get("project"),
        "branch": change.get("branch"),
        "change_id": change.get("change_id"),
        "subject": change.get("subject"),
        "status": change.get("status"),
        "owner": normalize_account(change.get("owner")),
        "updated": change.get("updated"),
        "current_revision": change.get("current_revision"),
        "current_patch_set": current_patch_set(change),
        "labels": change.get("labels") or {},
        "submit_requirements": change.get("submit_requirements") or [],
        "unresolved_comment_count": change.get("unresolved_comment_count", 0),
        "hashtags": change.get("hashtags") or [],
        "topic": change.get("topic"),
    }


def normalize_change_summaries(data: Any) -> Sequence[Dict[str, Any]]:
    if not isinstance(data, list):
        raise GerritParseError("Expected Gerrit change query response to be a JSON array.")
    if data and isinstance(data[0], list):
        changes = [item for group in data for item in group]
    else:
        changes = data
    summaries = []
    for change in changes:
        if not isinstance(change, Mapping):
            raise GerritParseError("Expected each Gerrit change query item to be an object.")
        summaries.append(normalize_change_summary(change))
    return summaries


def validate_pagination(args: argparse.Namespace) -> None:
    if args.limit <= 0:
        raise CLIUsageError("--limit must be greater than zero.")
    if args.start < 0:
        raise CLIUsageError("--start must be greater than or equal to zero.")


def query_options(args: argparse.Namespace) -> Sequence[str]:
    return args.option if args.option else DEFAULT_CHANGE_QUERY_OPTIONS


def query_changes(
    client: GerritClient,
    query: str,
    options: Sequence[str],
    limit: int,
    start: int,
) -> Sequence[Dict[str, Any]]:
    params = [("q", query), ("n", limit)]
    if start:
        params.append(("S", start))
    params.extend(("o", option) for option in options)
    response = client.get("/changes/", query=params)
    return normalize_change_summaries(response.data)


def handle_query_changes(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    validate_pagination(args)
    client = GerritClient.from_env(env)
    summaries = query_changes(
        client,
        args.query,
        query_options(args),
        args.limit,
        args.start,
    )
    return success_envelope("query-changes", summaries, args, env)


def build_preset_query(args: argparse.Namespace) -> str:
    template = QUERY_PRESETS[args.preset]
    if args.preset == "project_open" and not args.project:
        raise CLIUsageError("query-preset project_open requires --project.")
    query = template.format(project=args.project or "")
    if args.project and args.preset != "project_open":
        query += f" project:{args.project}"
    if args.branch:
        query += f" branch:{args.branch}"
    return query


def handle_query_preset(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    validate_pagination(args)
    client = GerritClient.from_env(env)
    summaries = query_changes(
        client,
        build_preset_query(args),
        query_options(args),
        args.limit,
        args.start,
    )
    return success_envelope("query-preset", summaries, args, env)


def handle_whoami(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    response = client.whoami()
    return success_envelope(
        "whoami",
        {
            "account": normalize_account(response.data),
            "status": response.status,
        },
        args,
        env,
    )


def gerrit_checks(env: Mapping[str, str]) -> Dict[str, Any]:
    checks: Dict[str, Any] = {
        "version": {"ok": False, "required": True},
        "whoami": {"ok": False, "required": True},
    }
    try:
        client = GerritClient.from_env(env)
    except GerritConfigError as exc:
        message = redact_message(exc, env)
        checks["version"].update({"message": message, "hint": "Fix Gerrit environment configuration first."})
        checks["whoami"].update({"message": message, "hint": "Fix Gerrit environment configuration first."})
        return checks

    try:
        response = client.version()
        checks["version"].update(
            {
                "ok": True,
                "status": response.status,
                "value": response.data,
            }
        )
    except GerritHTTPError as exc:
        checks["version"].update(
            {
                "ok": False,
                "status": exc.response.status,
                "message": redact_message(exc, env),
                "hint": http_error_hint(exc.response.status),
            }
        )
    except GerritClientError as exc:
        checks["version"].update({"ok": False, "message": redact_message(exc, env)})

    try:
        response = client.whoami()
        checks["whoami"].update(
            {
                "ok": True,
                "status": response.status,
                "account": normalize_account(response.data),
            }
        )
    except GerritHTTPError as exc:
        checks["whoami"].update(
            {
                "ok": False,
                "status": exc.response.status,
                "type": http_error_type(exc.response.status),
                "message": redact_message(exc, env),
                "hint": http_error_hint(exc.response.status),
            }
        )
    except GerritClientError as exc:
        checks["whoami"].update({"ok": False, "message": redact_message(exc, env)})

    return checks


def required_failures(data: Mapping[str, Any]) -> Sequence[str]:
    failures = []
    for section_name in ("dependencies", "environment", "gerrit"):
        section = data.get(section_name, {})
        if not isinstance(section, Mapping):
            continue
        for name, check in section.items():
            if isinstance(check, Mapping) and check.get("required") and not check.get("ok"):
                failures.append(f"{section_name}.{name}")
    xssi = data.get("xssi", {})
    if isinstance(xssi, Mapping) and xssi.get("required") and not xssi.get("ok"):
        failures.append("xssi")
    return failures


def doctor_envelope(
    data: Dict[str, Any],
    args: argparse.Namespace,
    env: Mapping[str, str],
    warnings: Sequence[str],
) -> Dict[str, Any]:
    failures = list(required_failures(data))
    doctor_ok = not failures
    data["doctor"] = {"ok": doctor_ok, "failed_required_checks": failures}
    if doctor_ok:
        return success_envelope("doctor", data, args, env, warnings=warnings)

    document = error_envelope(
        "doctor",
        "DoctorFailed",
        "One or more required doctor checks failed.",
        args,
        env,
        hint="Inspect data.doctor.failed_required_checks and each failed check hint.",
        warnings=warnings,
    )
    document["data"] = data
    return document


def handle_doctor(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    dependencies: Dict[str, Any] = {
        "python3": python_check(),
        "curl": command_check("curl", required=True),
        "git": command_check("git", required=True),
        "sed": command_check("sed", required=True),
        "jq": command_check("jq", required=False),
        "openssl": command_check("openssl", required=False, version_args=("version",)),
        "ssh": command_check("ssh", required=False, version_args=("-V",)),
        "rg": command_check("rg", required=False, version_args=("--version",)),
    }
    data = {
        "dependencies": dependencies,
        "environment": environment_checks(env),
        "gerrit": gerrit_checks(env),
        "xssi": xssi_check(),
        "cache": cache_check(env),
    }
    warnings = []
    for name, check in dependencies.items():
        if isinstance(check, Mapping) and not check.get("required") and not check.get("ok"):
            warnings.append(f"Optional command {name} is not available.")
    cache = data["cache"]
    if isinstance(cache, Mapping) and not cache.get("ok"):
        warnings.append("Gerrit cache directory is not writable; cache should be disabled.")
    return doctor_envelope(data, args, env, warnings)


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="gerrit_cli.py",
        description="active-gerrit command line tools",
    )
    parser.add_argument("--trace", help="Reserved Gerrit trace id to pass to future requests.")
    parser.add_argument("--deadline", help="Reserved Gerrit deadline, such as 5m or 30s.")
    parser.add_argument("--no-cache", action="store_true", help="Reserved flag to bypass local cache.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Check local dependencies, environment, and Gerrit connectivity.")
    doctor.set_defaults(handler=handle_doctor)
    ping = subparsers.add_parser("ping", help="Validate the CLI entrypoint without contacting Gerrit.")
    ping.set_defaults(handler=handle_ping)
    version = subparsers.add_parser("version", help="Fetch Gerrit server version.")
    version.set_defaults(handler=handle_version)
    whoami = subparsers.add_parser("whoami", help="Fetch the current authenticated Gerrit account.")
    whoami.set_defaults(handler=handle_whoami)
    query_changes_parser = subparsers.add_parser("query-changes", help="Query Gerrit changes.")
    query_changes_parser.add_argument("--query", required=True, help="Gerrit change query string.")
    query_changes_parser.add_argument(
        "--option",
        action="append",
        default=[],
        help="Gerrit ChangeInfo option. May be provided multiple times.",
    )
    query_changes_parser.add_argument("--limit", type=int, default=25, help="Maximum number of changes to return.")
    query_changes_parser.add_argument("--start", type=int, default=0, help="Pagination start offset.")
    query_changes_parser.set_defaults(handler=handle_query_changes)

    query_preset_parser = subparsers.add_parser("query-preset", help="Run a built-in Gerrit change query preset.")
    query_preset_parser.add_argument("preset", choices=sorted(QUERY_PRESETS), help="Preset query name.")
    query_preset_parser.add_argument("--project", help="Project filter or project for project_open.")
    query_preset_parser.add_argument("--branch", help="Branch filter.")
    query_preset_parser.add_argument(
        "--option",
        action="append",
        default=[],
        help="Gerrit ChangeInfo option. May be provided multiple times.",
    )
    query_preset_parser.add_argument("--limit", type=int, default=25, help="Maximum number of changes to return.")
    query_preset_parser.add_argument("--start", type=int, default=0, help="Pagination start offset.")
    query_preset_parser.set_defaults(handler=handle_query_preset)
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
        print_json(
            error_envelope(
                command_name(args),
                "ValidationError",
                exc,
                args,
                actual_env,
                hint="Run with --help to inspect available commands and options.",
            )
        )
        return EXIT_USAGE
    except GerritConfigError as exc:
        args = fallback_args(args)
        print_json(error_envelope(command_name(args), "ConfigError", exc, args, actual_env))
        return EXIT_CONFIG
    except GerritHTTPError as exc:
        args = fallback_args(args)
        status = exc.response.status
        print_json(
            error_envelope(
                command_name(args),
                http_error_type(status),
                exc,
                args,
                actual_env,
                status=status,
                hint=http_error_hint(status),
            )
        )
        return EXIT_FAILURE
    except GerritTransportError as exc:
        args = fallback_args(args)
        print_json(error_envelope(command_name(args), "TransportError", exc, args, actual_env))
        return EXIT_FAILURE
    except GerritParseError as exc:
        args = fallback_args(args)
        print_json(error_envelope(command_name(args), "ParseError", exc, args, actual_env))
        return EXIT_FAILURE
    except GerritClientError as exc:
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
