#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


CLI_PATH = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts" / "git_cli.py"


class GitCLISkeletonTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            cwd=str(CLI_PATH.parents[2]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def parse_stdout_json(self, result):
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

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

    def test_planned_command_outputs_json_not_implemented_error(self) -> None:
        result = self.run_cli("repo-status")
        self.assertEqual(result.returncode, 1)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "repo-status")
        self.assertEqual(payload["source"], "git")
        self.assertEqual(payload["error"]["type"], "NotImplemented")

    def test_usage_error_outputs_json(self) -> None:
        result = self.run_cli("missing-command")
        self.assertEqual(result.returncode, 2)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "git")
        self.assertEqual(payload["error"]["type"], "ValidationError")


if __name__ == "__main__":
    unittest.main()
