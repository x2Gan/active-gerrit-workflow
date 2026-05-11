#!/usr/bin/env python3
"""CLI entry point for active-gerrit local Git tools."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Mapping, Optional, Sequence

from git_runner import GitCommandError, GitConfigError, GitError, GitRunnerConfig
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


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if status:
            raise CLIUsageError((message or "").strip() or f"argparse exited with status {status}")
        raise SystemExit(status)


def runner_config_data(args: argparse.Namespace, env: Mapping[str, str]) -> Dict[str, Any]:
    config = GitRunnerConfig.from_args_env(args, env)
    return {
        "git_bin": config.git_bin,
        "repo": str(config.repo) if config.repo else None,
        "timeout_seconds": config.timeout_seconds,
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

    for name, help_text in PLANNED_COMMANDS:
        planned = subparsers.add_parser(name, help=help_text)
        planned.set_defaults(handler=handle_not_implemented)

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
                "GitConfigError",
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
