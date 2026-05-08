#!/usr/bin/env python3
"""CLI entry point for active-gerrit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from gerrit_client import (
    GerritClientError,
    GerritConfigError,
    GerritHTTPError,
    GerritParseError,
    GerritTransportError,
    redact_text,
)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

SOURCE = "gerrit"


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


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="gerrit_cli.py",
        description="active-gerrit command line tools",
    )
    parser.add_argument("--trace", help="Reserved Gerrit trace id to pass to future requests.")
    parser.add_argument("--deadline", help="Reserved Gerrit deadline, such as 5m or 30s.")
    parser.add_argument("--no-cache", action="store_true", help="Reserved flag to bypass local cache.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    ping = subparsers.add_parser("ping", help="Validate the CLI entrypoint without contacting Gerrit.")
    ping.set_defaults(handler=handle_ping)
    return parser


def print_json(document: Mapping[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


def run(argv: Optional[Sequence[str]] = None, env: Optional[Mapping[str, str]] = None) -> int:
    actual_env = os.environ if env is None else env
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        document = args.handler(args, actual_env)
        print_json(document)
        return EXIT_SUCCESS
    except SystemExit as exc:
        return int(exc.code or 0)
    except CLIUsageError as exc:
        args = argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)
        print_json(
            error_envelope(
                "unknown",
                "ValidationError",
                exc,
                args,
                actual_env,
                hint="Run with --help to inspect available commands and options.",
            )
        )
        return EXIT_USAGE
    except GerritConfigError as exc:
        args = argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)
        print_json(error_envelope(command_name(args), "ConfigError", exc, args, actual_env))
        return EXIT_CONFIG
    except GerritHTTPError as exc:
        args = argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)
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
        args = argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)
        print_json(error_envelope(command_name(args), "TransportError", exc, args, actual_env))
        return EXIT_FAILURE
    except GerritParseError as exc:
        args = argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)
        print_json(error_envelope(command_name(args), "ParseError", exc, args, actual_env))
        return EXIT_FAILURE
    except GerritClientError as exc:
        args = argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)
        print_json(error_envelope(command_name(args), type(exc).__name__, exc, args, actual_env))
        return EXIT_FAILURE
    except Exception as exc:  # pragma: no cover - last-resort safety net.
        args = argparse.Namespace(command="unknown", trace=None, deadline=None, no_cache=False)
        print_json(error_envelope(command_name(args), "UnexpectedError", exc, args, actual_env))
        return EXIT_FAILURE


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
