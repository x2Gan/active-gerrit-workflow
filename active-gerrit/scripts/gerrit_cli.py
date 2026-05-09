#!/usr/bin/env python3
"""CLI entry point for active-gerrit."""

from __future__ import annotations

import argparse
import base64
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
    quote_path_segment,
    redact_text,
)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

SOURCE = "gerrit"
IGNORE_WHITESPACE_OPTIONS = (
    "IGNORE_NONE",
    "IGNORE_TRAILING",
    "IGNORE_LEADING_AND_TRAILING",
    "IGNORE_ALL",
)
DEFAULT_CHANGE_QUERY_OPTIONS = (
    "CURRENT_REVISION",
    "DETAILED_ACCOUNTS",
    "LABELS",
    "SUBMIT_REQUIREMENTS",
)
DEFAULT_CHANGE_DETAIL_OPTIONS = (
    "CURRENT_REVISION",
    "DETAILED_ACCOUNTS",
    "DETAILED_LABELS",
    "SUBMIT_REQUIREMENTS",
)
CHANGE_DETAIL_LEVEL_OPTIONS = {
    "summary": DEFAULT_CHANGE_DETAIL_OPTIONS,
    "detail": DEFAULT_CHANGE_DETAIL_OPTIONS,
    "files": (
        *DEFAULT_CHANGE_DETAIL_OPTIONS,
        "CURRENT_COMMIT",
        "CURRENT_FILES",
    ),
    "full": (
        *DEFAULT_CHANGE_DETAIL_OPTIONS,
        "CURRENT_COMMIT",
        "CURRENT_FILES",
        "MESSAGES",
        "REVIEWER_UPDATES",
        "CURRENT_ACTIONS",
    ),
}
CHANGE_DETAIL_LEVELS = tuple(CHANGE_DETAIL_LEVEL_OPTIONS)

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


def unique_options(options: Iterable[str]) -> Sequence[str]:
    seen = set()
    unique = []
    for option in options:
        if option not in seen:
            unique.append(option)
            seen.add(option)
    return unique


def change_detail_options(detail_level: str) -> Sequence[str]:
    try:
        return unique_options(CHANGE_DETAIL_LEVEL_OPTIONS[detail_level])
    except KeyError as exc:
        allowed = ", ".join(CHANGE_DETAIL_LEVELS)
        raise CLIUsageError(f"--detail must be one of: {allowed}.") from exc


def change_endpoint(change: str, detail_level: str) -> str:
    encoded_change = quote_path_segment(change)
    if detail_level == "summary":
        return f"/changes/{encoded_change}"
    return f"/changes/{encoded_change}/detail"


def validate_change_arg(change: str) -> None:
    if not change or not change.strip():
        raise CLIUsageError("--change must not be empty.")


def normalize_file_summary(path: str, file_info: Any) -> Dict[str, Any]:
    if not isinstance(file_info, Mapping):
        file_info = {}
    return {
        "file": path,
        "status": file_info.get("status"),
        "old_path": file_info.get("old_path"),
        "lines_inserted": file_info.get("lines_inserted"),
        "lines_deleted": file_info.get("lines_deleted"),
        "size_delta": file_info.get("size_delta"),
        "size": file_info.get("size"),
        "old_mode": file_info.get("old_mode"),
        "new_mode": file_info.get("new_mode"),
    }


def normalize_file_summaries(files: Any) -> Sequence[Dict[str, Any]]:
    if not isinstance(files, Mapping):
        return []
    return [normalize_file_summary(path, file_info) for path, file_info in files.items()]


def normalize_revision_info(revision_id: str, revision: Any) -> Dict[str, Any]:
    if not isinstance(revision, Mapping):
        revision = {}
    raw_files = revision.get("files")
    files = normalize_file_summaries(raw_files)
    return {
        "revision": revision_id,
        "patch_set": revision.get("_number"),
        "created": revision.get("created"),
        "uploader": normalize_account(revision.get("uploader")),
        "ref": revision.get("ref"),
        "files_count": len(files) if isinstance(raw_files, Mapping) else None,
        "files": files,
        "fetch": revision.get("fetch") or {},
        "commit": revision.get("commit") or {},
        "actions": revision.get("actions") or {},
    }


def revision_sort_key(revision: Mapping[str, Any]) -> tuple:
    patch_set = revision.get("patch_set")
    if isinstance(patch_set, int):
        return (0, patch_set)
    return (1, str(revision.get("revision") or ""))


def normalize_revisions(revisions: Any) -> Sequence[Dict[str, Any]]:
    if not isinstance(revisions, Mapping):
        return []
    normalized = [normalize_revision_info(str(revision_id), revision) for revision_id, revision in revisions.items()]
    return sorted(normalized, key=revision_sort_key)


def normalize_reviewers(reviewers: Any) -> Dict[str, Sequence[Dict[str, Any]]]:
    result = {"REVIEWER": [], "CC": [], "REMOVED": []}
    if not isinstance(reviewers, Mapping):
        return result
    for state in result:
        accounts = reviewers.get(state) or []
        if isinstance(accounts, Sequence) and not isinstance(accounts, (str, bytes, bytearray)):
            result[state] = [normalize_account(account) for account in accounts]
    return result


def normalize_change_message(message: Any) -> Dict[str, Any]:
    if not isinstance(message, Mapping):
        return {}
    return {
        "id": message.get("id"),
        "date": message.get("date"),
        "author": normalize_account(message.get("author")),
        "real_author": normalize_account(message.get("real_author")),
        "message": message.get("message"),
        "tag": message.get("tag"),
        "revision_number": message.get("_revision_number"),
    }


def normalize_change_messages(messages: Any) -> Sequence[Dict[str, Any]]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        return []
    return [normalize_change_message(message) for message in messages if isinstance(message, Mapping)]


def normalize_reviewer_update(update: Any) -> Dict[str, Any]:
    if not isinstance(update, Mapping):
        return {}
    return {
        "updated": update.get("updated"),
        "updated_by": normalize_account(update.get("updated_by")),
        "reviewer": normalize_account(update.get("reviewer")),
        "state": update.get("state"),
    }


def normalize_reviewer_updates(updates: Any) -> Sequence[Dict[str, Any]]:
    if not isinstance(updates, Sequence) or isinstance(updates, (str, bytes, bytearray)):
        return []
    return [normalize_reviewer_update(update) for update in updates if isinstance(update, Mapping)]


def normalize_change_detail(change: Mapping[str, Any], include_raw: bool) -> Dict[str, Any]:
    return {
        "summary": normalize_change_summary(change),
        "revisions": normalize_revisions(change.get("revisions")),
        "reviewers": normalize_reviewers(change.get("reviewers")),
        "messages": normalize_change_messages(change.get("messages")),
        "reviewer_updates": normalize_reviewer_updates(change.get("reviewer_updates")),
        "actions": change.get("actions") or {},
        "raw": change if include_raw else None,
    }


def change_path_segment(change: str) -> str:
    validate_change_arg(change)
    return quote_path_segment(change)


def change_resource_path(change: str, suffix: str = "") -> str:
    base = f"/changes/{change_path_segment(change)}"
    return f"{base}/{suffix}" if suffix else base


def file_path_segment(file_path: str) -> str:
    if not file_path or not file_path.strip():
        raise CLIUsageError("--file must not be empty.")
    return quote_path_segment(file_path)


def revision_path_segment(revision: str) -> str:
    if not revision or not revision.strip():
        raise CLIUsageError("--revision must not be empty.")
    return quote_path_segment(revision)


def resolve_revision(client: GerritClient, change: str, revision: str) -> Dict[str, Any]:
    requested_revision = revision.strip()
    if requested_revision != "current":
        return {
            "requested_revision": requested_revision,
            "revision": requested_revision,
            "revision_sha": None,
            "patch_set": None,
        }

    response = client.get(
        f"/changes/{change_path_segment(change)}/detail",
        query=[("o", "CURRENT_REVISION")],
    )
    if not isinstance(response.data, Mapping):
        raise GerritParseError("Expected Gerrit change detail response to be a JSON object.")

    current_revision = response.data.get("current_revision")
    if not current_revision:
        raise GerritParseError("Gerrit change detail did not include current_revision.")

    patch_set = current_patch_set(response.data)
    resolved_revision = str(patch_set) if patch_set is not None else str(current_revision)
    return {
        "requested_revision": requested_revision,
        "revision": resolved_revision,
        "revision_sha": current_revision,
        "patch_set": patch_set,
    }


def revision_file_path(change: str, revision: str, suffix: str = "") -> str:
    return f"/changes/{change_path_segment(change)}/revisions/{revision_path_segment(revision)}/files/{suffix}"


def normalize_file_list(change: str, revision_info: Mapping[str, Any], files: Any) -> Dict[str, Any]:
    if not isinstance(files, Mapping):
        raise GerritParseError("Expected Gerrit file list response to be a JSON object.")
    return {
        "change": change,
        "revision": revision_info["revision"],
        "requested_revision": revision_info["requested_revision"],
        "revision_sha": revision_info["revision_sha"],
        "patch_set": revision_info["patch_set"],
        "files": normalize_file_summaries(files),
    }


def normalize_file_diff(
    change: str,
    revision_info: Mapping[str, Any],
    file_path: str,
    base: Optional[str],
    diff: Any,
) -> Dict[str, Any]:
    if not isinstance(diff, Mapping):
        raise GerritParseError("Expected Gerrit file diff response to be a JSON object.")
    return {
        "change": change,
        "revision": revision_info["revision"],
        "requested_revision": revision_info["requested_revision"],
        "revision_sha": revision_info["revision_sha"],
        "patch_set": revision_info["patch_set"],
        "base": base,
        "file": file_path,
        "change_type": diff.get("change_type"),
        "meta_a": diff.get("meta_a") or {},
        "meta_b": diff.get("meta_b") or {},
        "content": diff.get("content") or [],
        "diff_header": diff.get("diff_header") or [],
        "intraline_status": diff.get("intraline_status"),
        "web_links": diff.get("web_links") or [],
        "warnings": diff.get("warnings") or [],
    }


def content_encoding(content: Any, content_type: str) -> str:
    if not isinstance(content, str):
        return "json"
    if "text/plain" not in content_type.lower():
        return "text"
    try:
        base64.b64decode("".join(content.split()).encode("ascii"), validate=True)
    except Exception:
        return "text"
    return "base64"


def normalize_file_content(
    change: str,
    revision_info: Mapping[str, Any],
    file_path: str,
    content: Any,
    content_type: str,
) -> Dict[str, Any]:
    return {
        "change": change,
        "revision": revision_info["revision"],
        "requested_revision": revision_info["requested_revision"],
        "revision_sha": revision_info["revision_sha"],
        "patch_set": revision_info["patch_set"],
        "file": file_path,
        "content": content,
        "content_type": content_type,
        "encoding": content_encoding(content, content_type),
    }


def normalize_comment(comment: Any, fallback_path: str) -> Dict[str, Any]:
    if not isinstance(comment, Mapping):
        raise GerritParseError("Expected each Gerrit comment item to be a JSON object.")
    return {
        "id": comment.get("id"),
        "path": comment.get("path") or fallback_path,
        "side": comment.get("side"),
        "line": comment.get("line"),
        "range": comment.get("range"),
        "message": comment.get("message"),
        "updated": comment.get("updated"),
        "author": normalize_account(comment.get("author")),
        "unresolved": bool(comment.get("unresolved", False)),
        "in_reply_to": comment.get("in_reply_to"),
        "patch_set": comment.get("patch_set"),
        "commit_id": comment.get("commit_id"),
        "tag": comment.get("tag"),
    }


def normalize_comment_map(change: str, comments: Any, kind: str) -> Dict[str, Any]:
    if not isinstance(comments, Mapping):
        raise GerritParseError("Expected Gerrit comments response to be a JSON object.")

    comments_by_file: Dict[str, Sequence[Dict[str, Any]]] = {}
    files = []
    total_count = 0
    unresolved_count = 0
    for path, path_comments in comments.items():
        if not isinstance(path_comments, Sequence) or isinstance(path_comments, (str, bytes, bytearray)):
            raise GerritParseError("Expected each Gerrit comments file entry to be a JSON array.")
        normalized = [normalize_comment(comment, str(path)) for comment in path_comments]
        file_unresolved_count = sum(1 for comment in normalized if comment["unresolved"])
        total_count += len(normalized)
        unresolved_count += file_unresolved_count
        comments_by_file[str(path)] = normalized
        files.append(
            {
                "file": str(path),
                "comments": normalized,
                "count": len(normalized),
                "unresolved_count": file_unresolved_count,
            }
        )

    return {
        "change": change,
        "kind": kind,
        "comments_by_file": comments_by_file,
        "files": files,
        "total_count": total_count,
        "unresolved_count": unresolved_count,
    }


def normalize_message_list(change: str, messages: Any) -> Dict[str, Any]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        raise GerritParseError("Expected Gerrit messages response to be a JSON array.")
    normalized = normalize_change_messages(messages)
    return {
        "change": change,
        "messages": normalized,
        "total_count": len(normalized),
    }


def reviewer_counts(reviewers: Mapping[str, Sequence[Dict[str, Any]]]) -> Dict[str, int]:
    return {state: len(accounts) for state, accounts in reviewers.items()}


def normalize_reviewer_list(change: str, reviewers: Any) -> Dict[str, Any]:
    normalized = normalize_reviewers(reviewers)
    return {
        "change": change,
        "reviewers": normalized,
        "counts": reviewer_counts(normalized),
        "total_count": sum(len(accounts) for accounts in normalized.values()),
    }


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


def get_change(
    client: GerritClient,
    change: str,
    detail_level: str,
    include_raw: bool,
) -> Dict[str, Any]:
    validate_change_arg(change)
    params = [("o", option) for option in change_detail_options(detail_level)]
    response = client.get(change_endpoint(change, detail_level), query=params)
    if not isinstance(response.data, Mapping):
        raise GerritParseError("Expected Gerrit change response to be a JSON object.")
    return normalize_change_detail(response.data, include_raw=include_raw)


def handle_get_change(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    detail = get_change(
        client,
        args.change,
        args.detail,
        include_raw=args.include_raw,
    )
    return success_envelope("get-change", detail, args, env)


def list_files(client: GerritClient, change: str, revision: str) -> Dict[str, Any]:
    revision_info = resolve_revision(client, change, revision)
    response = client.get(revision_file_path(change, revision_info["revision"]))
    return normalize_file_list(change, revision_info, response.data)


def handle_list_files(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    files = list_files(client, args.change, args.revision)
    return success_envelope("list-files", files, args, env)


def diff_query_args(args: argparse.Namespace) -> Sequence[tuple]:
    if args.context is not None and args.context < 0:
        raise CLIUsageError("--context must be greater than or equal to zero.")
    params = []
    if args.base:
        params.append(("base", args.base))
    if args.context is not None:
        params.append(("context", args.context))
    if args.intraline:
        params.append(("intraline", "true"))
    if args.ignore_whitespace:
        params.append(("ignore-whitespace", args.ignore_whitespace))
    return params


def get_diff(client: GerritClient, args: argparse.Namespace) -> Dict[str, Any]:
    revision_info = resolve_revision(client, args.change, args.revision)
    path = revision_file_path(args.change, revision_info["revision"], f"{file_path_segment(args.file)}/diff")
    response = client.get(path, query=diff_query_args(args))
    return normalize_file_diff(args.change, revision_info, args.file, args.base, response.data)


def handle_get_diff(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    diff = get_diff(client, args)
    return success_envelope("get-diff", diff, args, env)


def get_content(client: GerritClient, change: str, revision: str, file_path: str) -> Dict[str, Any]:
    revision_info = resolve_revision(client, change, revision)
    path = revision_file_path(change, revision_info["revision"], f"{file_path_segment(file_path)}/content")
    response = client.get(path)
    return normalize_file_content(change, revision_info, file_path, response.data, response.content_type)


def handle_get_content(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    content = get_content(client, args.change, args.revision, args.file)
    return success_envelope("get-content", content, args, env)


def list_comments(client: GerritClient, change: str) -> Dict[str, Any]:
    response = client.get(change_resource_path(change, "comments"))
    return normalize_comment_map(change, response.data, "published")


def handle_list_comments(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    comments = list_comments(client, args.change)
    return success_envelope("list-comments", comments, args, env)


def list_drafts(client: GerritClient, change: str) -> Dict[str, Any]:
    response = client.get(change_resource_path(change, "drafts"))
    return normalize_comment_map(change, response.data, "draft")


def handle_list_drafts(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    drafts = list_drafts(client, args.change)
    return success_envelope("list-drafts", drafts, args, env)


def list_messages(client: GerritClient, change: str) -> Dict[str, Any]:
    response = client.get(change_resource_path(change, "messages"))
    return normalize_message_list(change, response.data)


def handle_list_messages(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    messages = list_messages(client, args.change)
    return success_envelope("list-messages", messages, args, env)


def list_reviewers(client: GerritClient, change: str) -> Dict[str, Any]:
    response = client.get(change_resource_path(change, "detail"), query=[("o", "DETAILED_ACCOUNTS")])
    if not isinstance(response.data, Mapping):
        raise GerritParseError("Expected Gerrit change detail response to be a JSON object.")
    return normalize_reviewer_list(change, response.data.get("reviewers"))


def handle_list_reviewers(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    client = GerritClient.from_env(env)
    reviewers = list_reviewers(client, args.change)
    return success_envelope("list-reviewers", reviewers, args, env)


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

    get_change_parser = subparsers.add_parser("get-change", help="Fetch and normalize a Gerrit change.")
    get_change_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    get_change_parser.add_argument(
        "--detail",
        choices=CHANGE_DETAIL_LEVELS,
        default="detail",
        help="Detail level to fetch: summary, detail, files, or full.",
    )
    get_change_parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include the raw Gerrit ChangeInfo payload in data.raw.",
    )
    get_change_parser.set_defaults(handler=handle_get_change)

    list_files_parser = subparsers.add_parser("list-files", help="List files for a Gerrit change revision.")
    list_files_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    list_files_parser.add_argument(
        "--revision",
        default="current",
        help="Revision id, patch set number, commit SHA, or current.",
    )
    list_files_parser.set_defaults(handler=handle_list_files)

    get_diff_parser = subparsers.add_parser("get-diff", help="Fetch and normalize a Gerrit file diff.")
    get_diff_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    get_diff_parser.add_argument(
        "--revision",
        default="current",
        help="Revision id, patch set number, commit SHA, or current.",
    )
    get_diff_parser.add_argument("--file", required=True, help="Repository file path, such as src/main/App.java.")
    get_diff_parser.add_argument("--base", help="Base revision or patch set to diff against.")
    get_diff_parser.add_argument("--context", type=int, help="Number of context lines to request.")
    get_diff_parser.add_argument("--intraline", action="store_true", help="Request Gerrit intraline diff data.")
    get_diff_parser.add_argument(
        "--ignore-whitespace",
        choices=IGNORE_WHITESPACE_OPTIONS,
        help="Gerrit whitespace handling mode.",
    )
    get_diff_parser.set_defaults(handler=handle_get_diff)

    get_content_parser = subparsers.add_parser("get-content", help="Fetch file content for a Gerrit change revision.")
    get_content_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    get_content_parser.add_argument(
        "--revision",
        default="current",
        help="Revision id, patch set number, commit SHA, or current.",
    )
    get_content_parser.add_argument("--file", required=True, help="Repository file path, such as src/main/App.java.")
    get_content_parser.set_defaults(handler=handle_get_content)

    list_comments_parser = subparsers.add_parser("list-comments", help="List published comments for a Gerrit change.")
    list_comments_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    list_comments_parser.set_defaults(handler=handle_list_comments)

    list_drafts_parser = subparsers.add_parser("list-drafts", help="List current user's draft comments for a Gerrit change.")
    list_drafts_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    list_drafts_parser.set_defaults(handler=handle_list_drafts)

    list_messages_parser = subparsers.add_parser("list-messages", help="List messages for a Gerrit change.")
    list_messages_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    list_messages_parser.set_defaults(handler=handle_list_messages)

    list_reviewers_parser = subparsers.add_parser("list-reviewers", help="List reviewers and CC for a Gerrit change.")
    list_reviewers_parser.add_argument("--change", required=True, help="Gerrit change id, preferably <project>~<number>.")
    list_reviewers_parser.set_defaults(handler=handle_list_reviewers)
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
