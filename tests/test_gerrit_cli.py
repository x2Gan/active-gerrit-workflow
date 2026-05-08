#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


CLI_PATH = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts" / "gerrit_cli.py"


class GerritCliTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        actual_env = os.environ.copy()
        actual_env.update(
            {
                "GERRIT_BASE_URL": "https://gerrit.example.com",
                "GERRIT_USERNAME": "alice",
                "GERRIT_HTTP_PASSWORD": "local-secret",
                "GERRIT_ACCESS_TOKEN": "access-secret",
            }
        )
        if env:
            actual_env.update(env)
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            cwd=str(CLI_PATH.parents[2]),
            env=actual_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_ping_success_outputs_json_envelope(self):
        result = self.run_cli("ping")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "ping")
        self.assertEqual(payload["source"], "gerrit")
        self.assertTrue(payload["data"]["ready"])
        self.assertIn("fetched_at", payload["meta"])

    def test_reserved_global_options_are_accepted(self):
        result = self.run_cli("--trace", "trace-123", "--deadline", "5m", "--no-cache", "ping")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["meta"]["trace"], "trace-123")
        self.assertEqual(payload["meta"]["deadline"], "5m")
        self.assertEqual(payload["meta"]["cache"], "bypass")

    def test_usage_error_outputs_json_without_stderr(self):
        result = self.run_cli("missing-command")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "ValidationError")
        self.assertIn("error", payload)

    def test_stderr_and_error_envelope_do_not_leak_env_secrets(self):
        result = self.run_cli("--bad-option")

        self.assertEqual(result.returncode, 2)
        self.assertNotIn("local-secret", result.stderr)
        self.assertNotIn("access-secret", result.stderr)
        self.assertNotIn("local-secret", result.stdout)
        self.assertNotIn("access-secret", result.stdout)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
