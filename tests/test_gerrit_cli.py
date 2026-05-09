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
from urllib import parse


CLI_PATH = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts" / "gerrit_cli.py"
EXPECTED_AUTH = "Basic " + base64.b64encode(b"alice:local-secret").decode("ascii")
FORBIDDEN_AUTH = "Basic " + base64.b64encode(b"alice:forbidden").decode("ascii")


class FakeDoctorGerritHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "headers": dict(self.headers.items()),
            }
        )
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
        parsed = parse.urlsplit(self.path)
        if parsed.path == "/a/changes/":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            query = parse.parse_qs(parsed.query)
            body = json.dumps(
                [
                    {
                        "id": "myProject~master~Iabc",
                        "_number": 4247,
                        "project": "myProject",
                        "branch": "master",
                        "change_id": "Iabc",
                        "subject": "Fix bug",
                        "status": "NEW",
                        "owner": {
                            "_account_id": 1000001,
                            "name": "Alice",
                            "email": "alice@example.com",
                            "username": "alice",
                        },
                        "updated": "2026-05-08 10:00:00.000000000",
                        "current_revision": "abc123",
                        "revisions": {"abc123": {"_number": 3}},
                        "labels": {"Code-Review": {}},
                        "submit_requirements": [{"name": "Code-Review", "status": "SATISFIED"}],
                        "unresolved_comment_count": 2,
                        "hashtags": ["feature-x"],
                        "topic": "feature-x",
                        "query_seen": query,
                    }
                ]
            ).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path in (
            "/a/changes/myProject~4247",
            "/a/changes/myProject~4247/detail",
            "/a/changes/platform%2Ffoo~4247/detail",
        ):
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            project = "platform/foo" if "platform%2Ffoo" in parsed.path else "myProject"
            options = parse.parse_qs(parsed.query).get("o", [])
            body = json.dumps(self._change_detail(project, options)).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        self._send(404, b"not found", "text/plain; charset=UTF-8")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def _change_detail(self, project, options):
        account = {
            "_account_id": 1000001,
            "name": "Alice",
            "email": "alice@example.com",
            "username": "alice",
        }
        reviewer = {
            "_account_id": 1000002,
            "name": "Bob",
            "email": "bob@example.com",
            "username": "bob",
        }
        data = {
            "id": f"{project}~master~Iabc",
            "_number": 4247,
            "project": project,
            "branch": "master",
            "change_id": "Iabc",
            "subject": "Fix bug",
            "status": "NEW",
            "created": "2026-05-07 10:00:00.000000000",
            "updated": "2026-05-08 10:00:00.000000000",
            "owner": account,
            "current_revision": "abc123",
            "revisions": {
                "abc123": {
                    "_number": 3,
                    "created": "2026-05-08 09:00:00.000000000",
                    "uploader": account,
                    "ref": "refs/changes/47/4247/3",
                    "fetch": {"http": {"url": "https://gerrit.example.com/myProject", "ref": "refs/changes/47/4247/3"}},
                    "commit": {
                        "commit": "abc123",
                        "subject": "Fix bug",
                        "message": "Fix bug\n\nChange-Id: Iabc",
                    },
                    "files": {
                        "/COMMIT_MSG": {"status": "M", "lines_inserted": 1, "lines_deleted": 0, "size_delta": 12, "size": 120},
                        "src/main/App.java": {
                            "status": "M",
                            "old_path": None,
                            "lines_inserted": 10,
                            "lines_deleted": 2,
                            "size_delta": 120,
                            "size": 4096,
                        },
                    },
                }
            },
            "labels": {
                "Code-Review": {
                    "approved": reviewer,
                    "all": [{"_account_id": 1000002, "value": 2}],
                }
            },
            "submit_requirements": [{"name": "Code-Review", "status": "SATISFIED"}],
            "reviewers": {"REVIEWER": [reviewer], "CC": [account]},
            "messages": [
                {
                    "id": "m1",
                    "date": "2026-05-08 09:01:00.000000000",
                    "author": account,
                    "message": "Patch Set 3: Uploaded patch set 3.",
                    "_revision_number": 3,
                    "tag": "autogenerated:upload",
                }
            ],
            "reviewer_updates": [
                {
                    "updated": "2026-05-08 09:02:00.000000000",
                    "updated_by": account,
                    "reviewer": reviewer,
                    "state": "REVIEWER",
                }
            ],
            "actions": {"submit": {"method": "POST", "label": "Submit"}},
            "unresolved_comment_count": 2,
            "hashtags": ["feature-x"],
            "topic": "feature-x",
        }
        revision = data["revisions"]["abc123"]
        if "CURRENT_COMMIT" not in options:
            revision.pop("commit", None)
        if "CURRENT_FILES" not in options:
            revision.pop("files", None)
        if "MESSAGES" not in options:
            data.pop("messages", None)
        if "REVIEWER_UPDATES" not in options:
            data.pop("reviewer_updates", None)
        if "CURRENT_ACTIONS" not in options:
            data.pop("actions", None)
        return data


class GerritCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeDoctorGerritHandler)
        cls.server.requests = []
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

    def latest_query(self):
        request = self.server.requests[-1]
        return parse.parse_qs(parse.urlsplit(request["path"]).query)

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

    def test_query_changes_returns_change_summaries_and_repeated_options(self):
        self.server.requests.clear()
        result = self.run_cli(
            "query-changes",
            "--query",
            "reviewer:self -owner:self status:open",
            "--option",
            "CURRENT_REVISION",
            "--option",
            "DETAILED_ACCOUNTS",
            "--limit",
            "10",
            "--start",
            "5",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "query-changes")
        self.assertEqual(len(payload["data"]), 1)
        summary = payload["data"][0]
        self.assertEqual(summary["id"], "myProject~4247")
        self.assertEqual(summary["triplet_id"], "myProject~master~Iabc")
        self.assertEqual(summary["current_patch_set"], 3)
        self.assertEqual(summary["owner"]["username"], "alice")
        self.assertEqual(summary["submit_requirements"][0]["status"], "SATISFIED")
        query = self.latest_query()
        self.assertEqual(query["q"], ["reviewer:self -owner:self status:open"])
        self.assertEqual(query["o"], ["CURRENT_REVISION", "DETAILED_ACCOUNTS"])
        self.assertEqual(query["n"], ["10"])
        self.assertEqual(query["S"], ["5"])

    def test_query_preset_project_open_supports_project_and_branch(self):
        self.server.requests.clear()
        result = self.run_cli(
            "query-preset",
            "project_open",
            "--project",
            "myProject",
            "--branch",
            "master",
            "--limit",
            "25",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "query-preset")
        query = self.latest_query()
        self.assertEqual(query["q"], ["project:myProject status:open branch:master"])

    def test_query_changes_default_options_are_not_heavy(self):
        self.server.requests.clear()
        result = self.run_cli(
            "query-preset",
            "my_open_reviews",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        query = self.latest_query()
        self.assertEqual(
            query["o"],
            ["CURRENT_REVISION", "DETAILED_ACCOUNTS", "LABELS", "SUBMIT_REQUIREMENTS"],
        )
        self.assertNotIn("CURRENT_FILES", query["o"])
        self.assertNotIn("MESSAGES", query["o"])
        self.assertNotIn("ALL_REVISIONS", query["o"])

    def test_query_preset_project_open_requires_project(self):
        result = self.run_cli("query-preset", "project_open", env=self.gerrit_env())

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "query-preset")
        self.assertIn("requires --project", payload["error"]["message"])

    def test_get_change_default_detail_returns_standard_change_detail(self):
        self.server.requests.clear()
        result = self.run_cli(
            "get-change",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "get-change")
        detail = payload["data"]
        self.assertEqual(detail["summary"]["id"], "myProject~4247")
        self.assertEqual(detail["summary"]["current_patch_set"], 3)
        self.assertEqual(detail["summary"]["labels"]["Code-Review"]["approved"]["username"], "bob")
        self.assertEqual(detail["summary"]["submit_requirements"][0]["status"], "SATISFIED")
        self.assertEqual(detail["revisions"][0]["patch_set"], 3)
        self.assertIsNone(detail["revisions"][0]["files_count"])
        self.assertEqual(detail["revisions"][0]["files"], [])
        self.assertEqual(detail["reviewers"]["REVIEWER"][0]["username"], "bob")
        self.assertIsNone(detail["raw"])
        request = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(request["path"]).path, "/a/changes/myProject~4247/detail")
        query = self.latest_query()
        self.assertEqual(
            query["o"],
            ["CURRENT_REVISION", "DETAILED_ACCOUNTS", "DETAILED_LABELS", "SUBMIT_REQUIREMENTS"],
        )

    def test_get_change_full_includes_messages_files_actions_and_raw(self):
        self.server.requests.clear()
        result = self.run_cli(
            "get-change",
            "--change",
            "myProject~4247",
            "--detail",
            "full",
            "--include-raw",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        detail = payload["data"]
        self.assertTrue(payload["ok"])
        self.assertEqual(detail["messages"][0]["revision_number"], 3)
        self.assertEqual(detail["reviewer_updates"][0]["state"], "REVIEWER")
        self.assertEqual(detail["actions"]["submit"]["method"], "POST")
        self.assertEqual(detail["revisions"][0]["commit"]["subject"], "Fix bug")
        self.assertEqual(detail["revisions"][0]["files"][1]["file"], "src/main/App.java")
        self.assertEqual(detail["raw"]["id"], "myProject~master~Iabc")
        query = self.latest_query()
        self.assertEqual(
            query["o"],
            [
                "CURRENT_REVISION",
                "DETAILED_ACCOUNTS",
                "DETAILED_LABELS",
                "SUBMIT_REQUIREMENTS",
                "CURRENT_COMMIT",
                "CURRENT_FILES",
                "MESSAGES",
                "REVIEWER_UPDATES",
                "CURRENT_ACTIONS",
            ],
        )

    def test_get_change_summary_uses_summary_endpoint(self):
        self.server.requests.clear()
        result = self.run_cli(
            "get-change",
            "--change",
            "myProject~4247",
            "--detail",
            "summary",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["summary"]["current_patch_set"], 3)
        request = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(request["path"]).path, "/a/changes/myProject~4247")

    def test_get_change_url_encodes_project_slash_in_change_id(self):
        self.server.requests.clear()
        result = self.run_cli(
            "get-change",
            "--change",
            "platform/foo~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["summary"]["id"], "platform/foo~4247")
        request = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(request["path"]).path, "/a/changes/platform%2Ffoo~4247/detail")


if __name__ == "__main__":
    unittest.main()
