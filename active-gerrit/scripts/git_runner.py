#!/usr/bin/env python3
"""Safe subprocess runner primitives for active-gerrit local Git commands."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from git_schemas import redact_text

DEFAULT_GIT_TIMEOUT_SECONDS = 60.0


class GitError(Exception):
    """Base class for local Git wrapper errors."""


class GitConfigError(GitError):
    """Local Git configuration or repository discovery failed."""


class GitCommandError(GitError):
    """A git command returned a non-zero exit code."""


class GitTimeoutError(GitCommandError):
    """A git command exceeded its timeout."""


@dataclass(frozen=True)
class GitRunnerConfig:
    git_bin: str
    repo: Optional[Path]
    timeout_seconds: float

    @classmethod
    def from_args_env(cls, args: object, env: Mapping[str, str]) -> "GitRunnerConfig":
        return cls(
            git_bin=(env.get("GIT_BIN") or "git").strip() or "git",
            repo=Path(str(getattr(args, "repo", ""))).expanduser() if getattr(args, "repo", None) else None,
            timeout_seconds=coerce_timeout(getattr(args, "timeout", None), env),
        )


@dataclass(frozen=True)
class GitCommandResult:
    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: Optional[str]


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


class GitRunner:
    """Run git through argv lists only; later commands should depend on this class."""

    def __init__(self, config: GitRunnerConfig, env: Mapping[str, str]):
        self.config = config
        self.env = env

    def run(self, args: Sequence[str], cwd: Optional[Path] = None, timeout: Optional[float] = None) -> GitCommandResult:
        if not args:
            raise GitConfigError("GitRunner.run requires at least one git subcommand argument.")

        command = [self.config.git_bin, *args]
        run_cwd = cwd or self.config.repo
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
            raise GitConfigError(f"Git executable not found: {self.config.git_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            message = f"Git command timed out after {timeout or self.config.timeout_seconds} seconds: git {args[0]}"
            raise GitTimeoutError(redact_text(message, self.env)) from exc

        result = GitCommandResult(
            args=tuple(args),
            returncode=completed.returncode,
            stdout=redact_text(completed.stdout, self.env),
            stderr=redact_text(completed.stderr, self.env),
            cwd=str(run_cwd) if run_cwd else None,
        )
        if completed.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"git {args[0]} failed"
            raise GitCommandError(detail)
        return result

    def resolve_repo_root(self, start: Optional[Path] = None) -> Path:
        probe = start or self.config.repo or Path.cwd()
        result = self.run(("rev-parse", "--show-toplevel"), cwd=probe)
        root = result.stdout.strip()
        if not root:
            raise GitConfigError("Could not resolve Git repository root.")
        return Path(root)
