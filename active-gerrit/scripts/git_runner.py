#!/usr/bin/env python3
"""Safe subprocess runner primitives for active-gerrit local Git commands."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from git_schemas import redact_text

DEFAULT_GIT_TIMEOUT_SECONDS = 60.0
DEFAULT_OUTPUT_LIMIT_CHARS = 128 * 1024


class GitError(Exception):
    """Base class for local Git wrapper errors."""


class GitConfigError(GitError):
    """Local Git configuration or repository discovery failed."""


class GitExecutableNotFound(GitConfigError):
    """The configured git executable could not be found."""


class GitNotRepositoryError(GitConfigError):
    """The target path is not inside a Git work tree."""


class GitCommandError(GitError):
    """A git command returned a non-zero exit code."""

    def __init__(
        self,
        message: object,
        *,
        args: Optional[Sequence[str]] = None,
        returncode: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
    ):
        super().__init__(str(message))
        self.args_vector = tuple(args or ())
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GitTimeoutError(GitCommandError):
    """A git command exceeded its timeout."""


@dataclass(frozen=True)
class GitRunnerConfig:
    git_bin: str
    repo: Optional[Path]
    timeout_seconds: float
    output_limit_chars: int

    @classmethod
    def from_args_env(cls, args: object, env: Mapping[str, str]) -> "GitRunnerConfig":
        return cls(
            git_bin=(env.get("GIT_BIN") or "git").strip() or "git",
            repo=Path(str(getattr(args, "repo", ""))).expanduser() if getattr(args, "repo", None) else None,
            timeout_seconds=coerce_timeout(getattr(args, "timeout", None), env),
            output_limit_chars=coerce_output_limit(getattr(args, "output_limit", None), env),
        )


@dataclass(frozen=True)
class GitCommandResult:
    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: Optional[str]
    stdout_truncated: bool = False
    stderr_truncated: bool = False


def coerce_timeout(value: object, env: Mapping[str, str]) -> float:
    raw = value if value is not None else env.get("GIT_TIMEOUT_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_GIT_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise GitConfigError("GIT_TIMEOUT_SECONDS or --timeout must be numeric.") from exc
    if timeout <= 0:
        raise GitConfigError("GIT_TIMEOUT_SECONDS or --timeout must be greater than zero.")
    return timeout


def coerce_output_limit(value: object, env: Mapping[str, str]) -> int:
    raw = value if value is not None else env.get("GIT_OUTPUT_LIMIT_CHARS") or env.get("GIT_OUTPUT_LIMIT_BYTES")
    if raw is None or raw == "":
        return DEFAULT_OUTPUT_LIMIT_CHARS
    try:
        limit = int(raw)
    except (TypeError, ValueError) as exc:
        raise GitConfigError("GIT_OUTPUT_LIMIT_CHARS must be an integer.") from exc
    if limit <= 0:
        raise GitConfigError("GIT_OUTPUT_LIMIT_CHARS must be greater than zero.")
    return limit


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = f"\n...[truncated {len(text) - limit} chars]..."
    if len(marker) >= limit:
        return marker[:limit], True
    keep = max(0, limit - len(marker))
    return text[:keep] + marker, True


def looks_like_not_repository(message: str) -> bool:
    normalized = message.lower()
    return "not a git repository" in normalized or "not a git work tree" in normalized


class GitRunner:
    """Run git through argv lists only; later commands should depend on this class."""

    def __init__(self, config: GitRunnerConfig, env: Mapping[str, str]):
        self.config = config
        self.env = env

    def run(
        self,
        args: Sequence[str],
        cwd: Optional[Path] = None,
        timeout: Optional[float] = None,
        check: bool = True,
    ) -> GitCommandResult:
        if not args:
            raise GitConfigError("GitRunner.run requires at least one git subcommand argument.")

        command = [self.config.git_bin, *args]
        run_cwd = cwd or self.config.repo
        if run_cwd is not None:
            if not run_cwd.exists():
                raise GitConfigError(f"Git working directory does not exist: {run_cwd}")
            if not run_cwd.is_dir():
                raise GitConfigError(f"Git working directory is not a directory: {run_cwd}")

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_cwd) if run_cwd else None,
                input="",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout or self.config.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitExecutableNotFound(f"Git executable not found: {self.config.git_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            stdout = decode_timeout_output(exc.stdout)
            stderr = decode_timeout_output(exc.stderr)
            stdout, _stdout_truncated = truncate_text(redact_text(stdout, self.env), self.config.output_limit_chars)
            stderr, _stderr_truncated = truncate_text(redact_text(stderr, self.env), self.config.output_limit_chars)
            message = f"Git command timed out after {timeout or self.config.timeout_seconds} seconds: git {args[0]}"
            if stderr:
                message = f"{message}: {stderr.strip()}"
            raise GitTimeoutError(
                redact_text(message, self.env),
                args=args,
                stdout=stdout,
                stderr=stderr,
            ) from exc

        stdout, stdout_truncated = truncate_text(redact_text(completed.stdout, self.env), self.config.output_limit_chars)
        stderr, stderr_truncated = truncate_text(redact_text(completed.stderr, self.env), self.config.output_limit_chars)
        result = GitCommandResult(
            args=tuple(args),
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            cwd=str(run_cwd) if run_cwd else None,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        if completed.returncode != 0 and check:
            detail = result.stderr.strip() or result.stdout.strip() or f"git {args[0]} failed"
            if looks_like_not_repository(detail):
                raise GitNotRepositoryError(detail)
            raise GitCommandError(
                detail,
                args=args,
                returncode=completed.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    def resolve_repo_root(self, start: Optional[Path] = None) -> Path:
        probe = start or self.config.repo or Path.cwd()
        try:
            result = self.run(("rev-parse", "--show-toplevel"), cwd=probe)
        except GitNotRepositoryError:
            raise
        except GitCommandError as exc:
            if looks_like_not_repository(str(exc)):
                raise GitNotRepositoryError(str(exc)) from exc
            raise
        root = result.stdout.strip()
        if not root:
            raise GitConfigError("Could not resolve Git repository root.")
        return Path(root)


def decode_timeout_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
