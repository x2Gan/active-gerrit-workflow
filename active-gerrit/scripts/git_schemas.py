#!/usr/bin/env python3
"""Shared JSON envelope helpers for active-gerrit local Git tools."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

SOURCE = "git"

SECRET_ENV_KEYS = (
    "GERRIT_HTTP_PASSWORD",
    "GERRIT_BEARER_TOKEN",
    "GERRIT_ACCESS_TOKEN",
    "GERRIT_COOKIE",
    "GERRIT_XSRF_TOKEN",
    "GIT_PASSWORD",
    "GIT_TOKEN",
)

URL_CREDENTIAL_RE = re.compile(r"(?P<scheme>https?://)(?P<credentials>[^/@\s]+)@")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def secret_values(env: Mapping[str, str]) -> Iterable[str]:
    for key in SECRET_ENV_KEYS:
        value = env.get(key)
        if value:
            yield value


def redact_text(value: object, env: Mapping[str, str]) -> str:
    text = str(value)
    for secret in secret_values(env):
        text = text.replace(secret, "<redacted>")
    return URL_CREDENTIAL_RE.sub(r"\g<scheme><redacted>@", text)


def command_name(args: object) -> str:
    return str(getattr(args, "command", None) or "unknown")


def fallback_args(args: Optional[object] = None) -> object:
    if args is not None:
        return args

    class FallbackArgs:
        command = "unknown"
        repo = None
        timeout = None
        trace = None
        dry_run = False
        yes = False

    return FallbackArgs()


def base_meta(args: object, env: Mapping[str, str]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "fetched_at": utc_now_iso(),
        "git_bin": redact_text(env.get("GIT_BIN") or "git", env),
    }
    repo = getattr(args, "repo", None)
    if repo:
        meta["repo"] = str(repo)
    timeout = getattr(args, "timeout", None)
    if timeout is not None:
        meta["timeout_seconds"] = timeout
    trace = getattr(args, "trace", None)
    if trace:
        meta["trace"] = trace
    return meta


def success_envelope(
    command: str,
    data: Any,
    args: object,
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
    args: object,
    env: Mapping[str, str],
    status: Optional[int] = None,
    hint: Optional[str] = None,
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    error: Dict[str, Any] = {
        "type": error_type,
        "message": redact_text(message, env),
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


def print_json(document: Mapping[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
