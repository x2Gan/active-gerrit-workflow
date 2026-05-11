#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from git_runner import (  # noqa: E402
    GitCommandError,
    GitExecutableNotFound,
    GitNotRepositoryError,
    GitRunner,
    GitRunnerConfig,
    GitTimeoutError,
    coerce_output_limit,
    coerce_timeout,
    truncate_text,
)


class GitRunnerTests(unittest.TestCase):
    def config(self, **overrides):
        values = {
            "git_bin": "git",
            "repo": None,
            "timeout_seconds": 5.0,
            "output_limit_chars": 1024,
        }
        values.update(overrides)
        return GitRunnerConfig(**values)

    def test_config_reads_env_and_args(self) -> None:
        args = SimpleNamespace(repo=".", timeout=7.5, output_limit=33)
        config = GitRunnerConfig.from_args_env(args, {"GIT_BIN": "/usr/bin/git"})
        self.assertEqual(config.git_bin, "/usr/bin/git")
        self.assertEqual(str(config.repo), ".")
        self.assertEqual(config.timeout_seconds, 7.5)
        self.assertEqual(config.output_limit_chars, 33)

    def test_coerce_timeout_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(Exception, "must be numeric"):
            coerce_timeout("slow", {})
        with self.assertRaisesRegex(Exception, "greater than zero"):
            coerce_timeout("0", {})

    def test_coerce_output_limit_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(Exception, "must be an integer"):
            coerce_output_limit("large", {})
        with self.assertRaisesRegex(Exception, "greater than zero"):
            coerce_output_limit("-1", {})

    def test_run_uses_argv_list_without_shell(self) -> None:
        runner = GitRunner(self.config(), {})
        completed = subprocess.CompletedProcess(["git", "--version"], 0, stdout="git version test\n", stderr="")
        with mock.patch("subprocess.run", return_value=completed) as run_mock:
            result = runner.run(("--version",))

        self.assertEqual(result.stdout, "git version test\n")
        positional_args, kwargs = run_mock.call_args
        self.assertEqual(positional_args[0], ["git", "--version"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["check"], False)

    def test_run_rejects_missing_working_directory_before_subprocess(self) -> None:
        missing = Path(tempfile.gettempdir()) / f"active-gerrit-missing-{time.time_ns()}"
        runner = GitRunner(self.config(repo=missing), {})
        with mock.patch("subprocess.run") as run_mock:
            with self.assertRaisesRegex(Exception, "does not exist"):
                runner.run(("status",))
        run_mock.assert_not_called()

    def test_run_distinguishes_missing_git_executable(self) -> None:
        runner = GitRunner(self.config(git_bin="/definitely/missing/git"), {})
        with self.assertRaises(GitExecutableNotFound):
            runner.run(("--version",))

    def test_run_distinguishes_command_failure(self) -> None:
        runner = GitRunner(self.config(), {})
        completed = subprocess.CompletedProcess(["git", "status"], 128, stdout="", stderr="fatal: custom failure\n")
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaises(GitCommandError) as context:
                runner.run(("status",))
        self.assertEqual(context.exception.returncode, 128)
        self.assertIn("custom failure", str(context.exception))

    def test_run_distinguishes_not_repository(self) -> None:
        runner = GitRunner(self.config(), {})
        completed = subprocess.CompletedProcess(
            ["git", "rev-parse", "--show-toplevel"],
            128,
            stdout="",
            stderr="fatal: not a git repository (or any of the parent directories): .git\n",
        )
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaises(GitNotRepositoryError):
                runner.resolve_repo_root()

    def test_run_distinguishes_timeout(self) -> None:
        runner = GitRunner(self.config(timeout_seconds=0.01), {})
        timeout = subprocess.TimeoutExpired(["git", "status"], 0.01, output=b"secret-token", stderr=b"still running")
        with mock.patch("subprocess.run", side_effect=timeout):
            with self.assertRaises(GitTimeoutError):
                runner.run(("status",), timeout=0.01)

    def test_run_redacts_and_truncates_output(self) -> None:
        runner = GitRunner(self.config(output_limit_chars=80), {"GIT_TOKEN": "secret-token"})
        completed = subprocess.CompletedProcess(
            ["git", "remote", "-v"],
            0,
            stdout="https://alice:secret-token@gerrit.example.com/project " + ("x" * 100),
            stderr="",
        )
        with mock.patch("subprocess.run", return_value=completed):
            result = runner.run(("remote", "-v"))

        self.assertNotIn("secret-token", result.stdout)
        self.assertIn("<redacted>", result.stdout)
        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(result.stdout), 80)

    def test_resolve_repo_root_returns_real_repo_root(self) -> None:
        repo = SCRIPTS_DIR.parents[1]
        runner = GitRunner(self.config(repo=repo), {})
        self.assertEqual(runner.resolve_repo_root(), repo)

    def test_truncate_text_marks_long_text(self) -> None:
        text, truncated = truncate_text("abcdef" * 10, 30)
        self.assertTrue(truncated)
        self.assertIn("truncated", text)
        self.assertLessEqual(len(text), 30)


if __name__ == "__main__":
    unittest.main()
