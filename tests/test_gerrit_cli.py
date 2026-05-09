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
        if parsed.path in (
            "/a/changes/myProject~4247/revisions/3/files/",
            "/a/changes/myProject~4247/revisions/2/files/",
        ):
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self._files()).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path in (
            "/a/changes/myProject~4247/revisions/3/files/src%2Fmain%2FApp.java/diff",
            "/a/changes/myProject~4247/revisions/2/files/src%2Fmain%2FApp.java/diff",
        ):
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self._diff()).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/revisions/3/files/%2FCOMMIT_MSG/content":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = base64.b64encode(b"Fix bug\n\nChange-Id: Iabc\n")
            self._send(200, body, "text/plain; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/comments":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self._comments()).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/drafts":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self._drafts()).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/messages":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self._messages()).encode("utf-8")
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

    def _files(self):
        return {
            "/COMMIT_MSG": {
                "status": "M",
                "lines_inserted": 1,
                "lines_deleted": 0,
                "size_delta": 12,
                "size": 120,
            },
            "src/main/App.java": {
                "status": "M",
                "old_path": None,
                "lines_inserted": 10,
                "lines_deleted": 2,
                "size_delta": 120,
                "size": 4096,
            },
        }

    def _diff(self):
        return {
            "change_type": "MODIFIED",
            "meta_a": {"name": "src/main/App.java", "content_type": "text/x-java"},
            "meta_b": {"name": "src/main/App.java", "content_type": "text/x-java"},
            "intraline_status": "OK",
            "diff_header": ["diff --git a/src/main/App.java b/src/main/App.java"],
            "content": [
                {"ab": ["class App {"]},
                {"a": ["  int oldValue;"], "b": ["  int newValue;"]},
            ],
            "web_links": [{"name": "gitweb", "url": "https://gerrit.example.com/gitweb"}],
        }

    def _comments(self):
        return {
            "src/main/App.java": [
                {
                    "id": "c1",
                    "path": "src/main/App.java",
                    "side": "REVISION",
                    "line": 42,
                    "range": {
                        "start_line": 42,
                        "start_character": 4,
                        "end_line": 42,
                        "end_character": 13,
                    },
                    "message": "Please rename this variable.",
                    "updated": "2026-05-08 09:30:00.000000000",
                    "author": {
                        "_account_id": 1000002,
                        "name": "Bob",
                        "email": "bob@example.com",
                        "username": "bob",
                    },
                    "unresolved": True,
                    "patch_set": 3,
                    "commit_id": "abc123",
                },
                {
                    "id": "c2",
                    "path": "src/main/App.java",
                    "side": "PARENT",
                    "line": 12,
                    "message": "Resolved context.",
                    "updated": "2026-05-08 09:35:00.000000000",
                    "author": {
                        "_account_id": 1000001,
                        "name": "Alice",
                        "email": "alice@example.com",
                        "username": "alice",
                    },
                    "unresolved": False,
                    "in_reply_to": "c1",
                },
            ],
            "/PATCHSET_LEVEL": [
                {
                    "id": "c3",
                    "side": "REVISION",
                    "message": "Overall direction looks good.",
                    "updated": "2026-05-08 09:40:00.000000000",
                    "author": {
                        "_account_id": 1000002,
                        "name": "Bob",
                        "email": "bob@example.com",
                        "username": "bob",
                    },
                    "unresolved": False,
                    "tag": "autogenerated:active-gerrit",
                }
            ],
        }

    def _drafts(self):
        return {
            "src/main/App.java": [
                {
                    "id": "d1",
                    "path": "src/main/App.java",
                    "side": "REVISION",
                    "line": 99,
                    "message": "Draft note.",
                    "updated": "2026-05-08 10:00:00.000000000",
                    "unresolved": True,
                    "patch_set": 3,
                }
            ]
        }

    def _messages(self):
        return [
            {
                "id": "m1",
                "date": "2026-05-08 09:01:00.000000000",
                "author": {
                    "_account_id": 1000001,
                    "name": "Alice",
                    "email": "alice@example.com",
                    "username": "alice",
                },
                "message": "Patch Set 3: Uploaded patch set 3.",
                "_revision_number": 3,
                "tag": "autogenerated:upload",
            },
            {
                "id": "m2",
                "date": "2026-05-08 09:45:00.000000000",
                "author": {
                    "_account_id": 1000002,
                    "name": "Bob",
                    "email": "bob@example.com",
                    "username": "bob",
                },
                "real_author": {
                    "_account_id": 1000002,
                    "name": "Bob",
                    "email": "bob@example.com",
                    "username": "bob",
                },
                "message": "Patch Set 3: Code-Review+2",
                "_revision_number": 3,
            },
        ]


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

    def test_list_files_resolves_current_revision_and_returns_file_summaries(self):
        self.server.requests.clear()
        result = self.run_cli(
            "list-files",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "list-files")
        data = payload["data"]
        self.assertEqual(data["change"], "myProject~4247")
        self.assertEqual(data["requested_revision"], "current")
        self.assertEqual(data["revision"], "3")
        self.assertEqual(data["revision_sha"], "abc123")
        self.assertEqual(data["patch_set"], 3)
        self.assertEqual(data["files"][1]["file"], "src/main/App.java")
        self.assertEqual(data["files"][1]["lines_inserted"], 10)
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths[0], "/a/changes/myProject~4247/detail")
        self.assertEqual(paths[1], "/a/changes/myProject~4247/revisions/3/files/")

    def test_get_diff_encodes_file_path_and_preserves_gerrit_fields(self):
        self.server.requests.clear()
        result = self.run_cli(
            "get-diff",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            "--file",
            "src/main/App.java",
            "--base",
            "2",
            "--context",
            "50",
            "--intraline",
            "--ignore-whitespace",
            "IGNORE_TRAILING",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "get-diff")
        diff = payload["data"]
        self.assertEqual(diff["change"], "myProject~4247")
        self.assertEqual(diff["revision"], "3")
        self.assertEqual(diff["requested_revision"], "current")
        self.assertEqual(diff["base"], "2")
        self.assertEqual(diff["file"], "src/main/App.java")
        self.assertEqual(diff["change_type"], "MODIFIED")
        self.assertEqual(diff["meta_a"]["name"], "src/main/App.java")
        self.assertEqual(diff["meta_b"]["content_type"], "text/x-java")
        self.assertEqual(diff["intraline_status"], "OK")
        self.assertEqual(diff["diff_header"][0], "diff --git a/src/main/App.java b/src/main/App.java")
        self.assertEqual(diff["content"][1]["b"], ["  int newValue;"])
        self.assertEqual(diff["web_links"][0]["name"], "gitweb")
        request = self.server.requests[-1]
        self.assertEqual(
            parse.urlsplit(request["path"]).path,
            "/a/changes/myProject~4247/revisions/3/files/src%2Fmain%2FApp.java/diff",
        )
        query = self.latest_query()
        self.assertEqual(query["base"], ["2"])
        self.assertEqual(query["context"], ["50"])
        self.assertEqual(query["intraline"], ["true"])
        self.assertEqual(query["ignore-whitespace"], ["IGNORE_TRAILING"])

    def test_get_diff_explicit_revision_does_not_resolve_current(self):
        self.server.requests.clear()
        result = self.run_cli(
            "get-diff",
            "--change",
            "myProject~4247",
            "--revision",
            "2",
            "--file",
            "src/main/App.java",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["revision"], "2")
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths, ["/a/changes/myProject~4247/revisions/2/files/src%2Fmain%2FApp.java/diff"])

    def test_get_content_encodes_special_file_and_reports_base64_content(self):
        self.server.requests.clear()
        result = self.run_cli(
            "get-content",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            "--file",
            "/COMMIT_MSG",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "get-content")
        data = payload["data"]
        self.assertEqual(data["revision"], "3")
        self.assertEqual(data["file"], "/COMMIT_MSG")
        self.assertEqual(data["encoding"], "base64")
        self.assertEqual(base64.b64decode(data["content"]).decode("utf-8"), "Fix bug\n\nChange-Id: Iabc\n")
        request = self.server.requests[-1]
        self.assertEqual(
            parse.urlsplit(request["path"]).path,
            "/a/changes/myProject~4247/revisions/3/files/%2FCOMMIT_MSG/content",
        )

    def test_list_comments_groups_by_file_and_counts_unresolved(self):
        self.server.requests.clear()
        result = self.run_cli(
            "list-comments",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "list-comments")
        data = payload["data"]
        self.assertEqual(data["kind"], "published")
        self.assertEqual(data["total_count"], 3)
        self.assertEqual(data["unresolved_count"], 1)
        app_comments = data["comments_by_file"]["src/main/App.java"]
        self.assertEqual(len(app_comments), 2)
        self.assertTrue(app_comments[0]["unresolved"])
        self.assertEqual(app_comments[0]["author"]["username"], "bob")
        self.assertEqual(app_comments[0]["range"]["start_line"], 42)
        self.assertEqual(app_comments[1]["in_reply_to"], "c1")
        patchset_comments = data["comments_by_file"]["/PATCHSET_LEVEL"]
        self.assertEqual(patchset_comments[0]["path"], "/PATCHSET_LEVEL")
        request = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(request["path"]).path, "/a/changes/myProject~4247/comments")

    def test_list_drafts_groups_current_user_drafts(self):
        self.server.requests.clear()
        result = self.run_cli(
            "list-drafts",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "list-drafts")
        data = payload["data"]
        self.assertEqual(data["kind"], "draft")
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(data["unresolved_count"], 1)
        draft = data["comments_by_file"]["src/main/App.java"][0]
        self.assertEqual(draft["id"], "d1")
        self.assertEqual(draft["author"], {})
        self.assertTrue(draft["unresolved"])
        request = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(request["path"]).path, "/a/changes/myProject~4247/drafts")

    def test_list_messages_returns_normalized_change_messages(self):
        self.server.requests.clear()
        result = self.run_cli(
            "list-messages",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "list-messages")
        data = payload["data"]
        self.assertEqual(data["change"], "myProject~4247")
        self.assertEqual(data["total_count"], 2)
        self.assertEqual(data["messages"][0]["revision_number"], 3)
        self.assertEqual(data["messages"][0]["author"]["username"], "alice")
        self.assertEqual(data["messages"][1]["real_author"]["username"], "bob")
        request = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(request["path"]).path, "/a/changes/myProject~4247/messages")

    def test_list_reviewers_returns_reviewers_and_cc(self):
        self.server.requests.clear()
        result = self.run_cli(
            "list-reviewers",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "list-reviewers")
        data = payload["data"]
        self.assertEqual(data["reviewers"]["REVIEWER"][0]["username"], "bob")
        self.assertEqual(data["reviewers"]["CC"][0]["username"], "alice")
        self.assertEqual(data["counts"], {"REVIEWER": 1, "CC": 1, "REMOVED": 0})
        self.assertEqual(data["total_count"], 2)
        request = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(request["path"]).path, "/a/changes/myProject~4247/detail")
        self.assertEqual(self.latest_query()["o"], ["DETAILED_ACCOUNTS"])


if __name__ == "__main__":
    unittest.main()
