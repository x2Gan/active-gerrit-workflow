#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CLI_PATH = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts" / "git_cli.py"


class GitCLISkeletonTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None, env=None):
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            cwd=str(cwd or CLI_PATH.parents[2]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def parse_stdout_json(self, result):
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def run_git(self, repo, *args):
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def make_repo(self, with_commit=True):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repo = Path(temp_dir.name)
        self.run_git(repo, "init", "-q")
        self.run_git(repo, "config", "user.name", "Tester")
        self.run_git(repo, "config", "user.email", "tester@example.com")
        if with_commit:
            (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
            self.run_git(repo, "add", "tracked.txt")
            self.run_git(repo, "commit", "-q", "-m", "initial")
        return repo

    def test_help_is_available(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("active-gerrit local Git command line tools", result.stdout)
        self.assertIn("repo-status", result.stdout)

    def test_ping_outputs_git_envelope(self) -> None:
        result = self.run_cli("ping")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "ping")
        self.assertEqual(payload["source"], "git")
        self.assertEqual(payload["data"]["cli"], "active-gerrit-git")
        self.assertIn("push-review", payload["data"]["planned_commands"])

    def test_global_options_are_reflected_in_ping(self) -> None:
        result = self.run_cli("--repo", ".", "--timeout", "7", "--trace", "trace-1", "--dry-run", "ping")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertEqual(payload["data"]["reserved_options"]["repo"], ".")
        self.assertEqual(payload["data"]["reserved_options"]["timeout"], 7.0)
        self.assertEqual(payload["meta"]["timeout_seconds"], 7.0)
        self.assertEqual(payload["meta"]["trace"], "trace-1")
        self.assertTrue(payload["data"]["reserved_options"]["dry_run"])

    def test_not_implemented_command_outputs_json_error(self) -> None:
        result = self.run_cli("repo-diff")
        self.assertEqual(result.returncode, 1)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "repo-diff")
        self.assertEqual(payload["source"], "git")
        self.assertEqual(payload["error"]["type"], "NotImplemented")

    def test_usage_error_outputs_json(self) -> None:
        result = self.run_cli("missing-command")
        self.assertEqual(result.returncode, 2)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "git")
        self.assertEqual(payload["error"]["type"], "ValidationError")

    def test_repo_info_warns_when_no_upstream(self) -> None:
        repo = self.make_repo()
        branch = self.run_git(repo, "branch", "--show-current").stdout.strip()
        result = self.run_cli("--repo", str(repo), "repo-info")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["repo_root"], str(repo))
        self.assertEqual(payload["data"]["current_branch"], branch)
        self.assertIsNone(payload["data"]["upstream"])
        self.assertIn("No upstream branch is configured for the current branch.", payload["warnings"])

    def test_repo_status_reports_clean_repo(self) -> None:
        repo = self.make_repo()
        result = self.run_cli("--repo", str(repo), "repo-status")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["data"]["is_clean"])
        self.assertEqual(payload["data"]["staged"], [])
        self.assertEqual(payload["data"]["unstaged"], [])
        self.assertEqual(payload["data"]["untracked"], [])
        self.assertEqual(payload["data"]["conflicts"], [])

    def test_repo_status_reports_staged_unstaged_and_untracked_changes(self) -> None:
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("initial\nchanged\n", encoding="utf-8")
        (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        self.run_git(repo, "add", "staged.txt")
        (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        result = self.run_cli("--repo", str(repo), "repo-status")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["data"]["is_clean"])
        self.assertEqual([item["path"] for item in payload["data"]["staged"]], ["staged.txt"])
        self.assertEqual([item["path"] for item in payload["data"]["unstaged"]], ["tracked.txt"])
        self.assertEqual([item["path"] for item in payload["data"]["untracked"]], ["untracked.txt"])

    def test_repo_remotes_redacts_credentials(self) -> None:
        repo = self.make_repo()
        self.run_git(repo, "remote", "add", "origin", "https://alice:secret-token@example.com/project.git")
        result = self.run_cli("--repo", str(repo), "repo-remotes")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("secret-token", result.stdout)
        payload = self.parse_stdout_json(result)
        self.assertEqual(payload["data"]["remotes"][0]["name"], "origin")
        self.assertIn("<redacted>", payload["data"]["remotes"][0]["fetch_url"])

    def test_repo_config_reports_identity_and_hook_status(self) -> None:
        repo = self.make_repo()
        branch = self.run_git(repo, "branch", "--show-current").stdout.strip()
        result = self.run_cli("--repo", str(repo), "repo-config")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertEqual(payload["data"]["config"]["user.name"]["value"], "Tester")
        self.assertEqual(payload["data"]["config"]["user.email"]["value"], "tester@example.com")
        self.assertIn(f"branch.{branch}.remote", payload["data"]["config"])
        self.assertFalse(payload["data"]["hooks"]["commit_msg"]["ok"])

    def test_git_doctor_succeeds_without_upstream(self) -> None:
        repo = self.make_repo()
        result = self.run_cli("--repo", str(repo), "git-doctor")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["doctor"]["ok"])
        self.assertTrue(payload["data"]["repository"]["repo"]["ok"])
        self.assertFalse(payload["data"]["repository"]["upstream"]["ok"])
        self.assertIn("No upstream branch is configured for the current branch.", payload["warnings"])
        self.assertIn("commit-msg hook is not installed for this repository.", payload["warnings"])

    def test_git_doctor_outside_repo_returns_doctor_failed_json(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env = dict(os.environ)
        env["HOME"] = temp_dir.name
        result = self.run_cli("--repo", temp_dir.name, "git-doctor", env=env)
        self.assertEqual(result.returncode, 1)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "DoctorFailed")
        self.assertIn("repository.repo", payload["data"]["doctor"]["failed_required_checks"])


if __name__ == "__main__":
    unittest.main()
