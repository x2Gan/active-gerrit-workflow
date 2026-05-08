#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import os
import tempfile
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


CLI_PATH = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts" / "gerrit_cli.py"
EXPECTED_AUTH = "Basic " + base64.b64encode(b"alice:local-secret").decode("ascii")
FORBIDDEN_AUTH = "Basic " + base64.b64encode(b"alice:forbidden").decode("ascii")


class FakeDoctorGerritHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):
        if self.path == "/config/server/version":
            self._send(200, b")]}'\n\"3.11.2\"", "application/json; charset=UTF-8")
            return
        if self.path == "/a/accounts/self/detail":
            auth = self.headers.get("Authorization")
            if auth == EXPECTED_AUTH:
                body = b")]}'\n{\"_account_id\":1000001,\"username\":\"alice\",\"email\":\"alice@example.com\"}"
                self._send(200, body, "application/json; charset=UTF-8")
                return
            if auth == FORBIDDEN_AUTH:
                self._send(403, b"forbidden", "text/plain; charset=UTF-8")
                return
            self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
            return
        self._send(404, b"not found", "text/plain; charset=UTF-8")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)


class GerritCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeDoctorGerritHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

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

    def fake_path(self, *commands):
        temp_dir = tempfile.TemporaryDirectory()
        for command in commands:
            path = Path(temp_dir.name) / command
            path.write_text(f"#!/bin/sh\necho '{command} fake 1.0'\n", encoding="utf-8")
            path.chmod(0o755)
        return temp_dir, temp_dir.name

    def doctor_env(self, fake_path, **overrides):
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        env = {
            "PATH": fake_path,
            "GERRIT_BASE_URL": self.base_url,
            "GERRIT_AUTH_TYPE": "basic",
            "GERRIT_USERNAME": "alice",
            "GERRIT_HTTP_PASSWORD": "local-secret",
            "GERRIT_CACHE_DIR": cache_dir.name,
        }
        env.update(overrides)
        return env

    def gerrit_env(self, **overrides):
        env = {
            "GERRIT_BASE_URL": self.base_url,
            "GERRIT_AUTH_TYPE": "basic",
            "GERRIT_USERNAME": "alice",
            "GERRIT_HTTP_PASSWORD": "local-secret",
        }
        env.update(overrides)
        return env

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

    def test_doctor_success_outputs_health_report(self):
        temp_dir, fake_path = self.fake_path("curl", "git", "sed")
        self.addCleanup(temp_dir.cleanup)

        result = self.run_cli("doctor", env=self.doctor_env(fake_path))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "doctor")
        self.assertTrue(payload["data"]["doctor"]["ok"])
        self.assertEqual(payload["data"]["gerrit"]["version"]["value"], "3.11.2")
        self.assertEqual(payload["data"]["gerrit"]["whoami"]["account"]["username"], "alice")
        self.assertTrue(payload["data"]["xssi"]["ok"])
        self.assertTrue(payload["data"]["cache"]["ok"])

    def test_doctor_missing_curl_has_install_hint(self):
        temp_dir, fake_path = self.fake_path("git", "sed")
        self.addCleanup(temp_dir.cleanup)

        result = self.run_cli("doctor", env=self.doctor_env(fake_path))

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        curl = payload["data"]["dependencies"]["curl"]
        self.assertFalse(curl["ok"])
        self.assertTrue(curl["required"])
        self.assertIn("Install curl", curl["hint"])
        self.assertIn("dependencies.curl", payload["data"]["doctor"]["failed_required_checks"])

    def test_doctor_distinguishes_auth_401_and_403(self):
        temp_dir, fake_path = self.fake_path("curl", "git", "sed")
        self.addCleanup(temp_dir.cleanup)

        cases = [("wrong-secret", 401, "GerritAuthError"), ("forbidden", 403, "GerritPermissionError")]
        for password, status, error_type in cases:
            with self.subTest(status=status):
                result = self.run_cli(
                    "doctor",
                    env=self.doctor_env(fake_path, GERRIT_HTTP_PASSWORD=password),
                )

                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "")
                self.assertNotIn(password, result.stdout)
                payload = json.loads(result.stdout)
                whoami = payload["data"]["gerrit"]["whoami"]
                self.assertFalse(whoami["ok"])
                self.assertEqual(whoami["status"], status)
                self.assertEqual(whoami["type"], error_type)

    def test_version_command_returns_gerrit_version(self):
        result = self.run_cli("version", env=self.gerrit_env())

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "version")
        self.assertEqual(payload["data"]["version"], "3.11.2")
        self.assertEqual(payload["data"]["status"], 200)

    def test_whoami_command_returns_standard_account_fields(self):
        result = self.run_cli("whoami", env=self.gerrit_env())

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        account = payload["data"]["account"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "whoami")
        self.assertEqual(account["_account_id"], 1000001)
        self.assertEqual(account["account_id"], 1000001)
        self.assertEqual(account["username"], "alice")
        self.assertEqual(account["email"], "alice@example.com")


if __name__ == "__main__":
    unittest.main()
