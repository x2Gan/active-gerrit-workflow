#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKFLOW_CLI_PATH = Path(__file__).resolve().parents[1] / "active-gerrit-workflow" / "scripts" / "workflow_cli.py"


class WorkflowCliTests(unittest.TestCase):
    def run_cli(self, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKFLOW_CLI_PATH), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            check=False,
        )

    def write_active_gerrit_stub(self, root: Path, body: str) -> Path:
        active_home = root / "active-gerrit"
        scripts_dir = active_home / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / "gerrit_cli.py"
        script_path.write_text(textwrap.dedent(body), encoding="utf-8")
        return active_home

    def test_doctor_wraps_active_gerrit_doctor_and_records_used_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active_home = self.write_active_gerrit_stub(
                root,
                """
                import json
                import sys

                document = {
                    "ok": True,
                    "command": "doctor",
                    "source": "gerrit",
                    "data": {
                        "argv": sys.argv[1:],
                        "doctor": {
                            "ok": True,
                        },
                    },
                    "warnings": ["base warning"],
                    "meta": {"fetched_at": "2026-05-08T10:00:00+00:00"},
                }
                print(json.dumps(document, sort_keys=True))
                """,
            )
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "ACTIVE_GERRIT_HOME": str(root),
            }

            completed = self.run_cli("--trace", "trace-123", "--no-cache", "doctor", env=env)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            document = json.loads(completed.stdout)
            self.assertTrue(document["ok"])
            self.assertEqual(document["workflow"], "doctor")
            self.assertEqual(document["decision"]["status"], "pass")
            self.assertEqual(document["used_active_gerrit_commands"], ["doctor"])
            self.assertEqual(document["target"]["active_gerrit_home"], str(active_home))
            self.assertEqual(document["target"]["active_gerrit_cli"], str(active_home / "scripts" / "gerrit_cli.py"))
            self.assertIn("active-gerrit doctor: base warning", document["warnings"])
            checks = {entry["name"]: entry for entry in document["checks"]}
            self.assertEqual(checks["active_gerrit_doctor"]["status"], "passed")
            self.assertEqual(
                checks["active_gerrit_doctor"]["details"]["data"]["argv"],
                ["--trace", "trace-123", "--no-cache", "doctor"],
            )
            self.assertEqual(document["meta"]["active_gerrit_home_source"], "env")

    def test_doctor_reports_missing_active_gerrit_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "ACTIVE_GERRIT_HOME": temp_dir,
            }

            completed = self.run_cli("doctor", env=env)

            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertEqual(completed.stderr, "")
            document = json.loads(completed.stdout)
            self.assertFalse(document["ok"])
            self.assertEqual(document["workflow"], "doctor")
            self.assertEqual(document["decision"]["status"], "blocked")
            self.assertEqual(document["used_active_gerrit_commands"], [])
            self.assertEqual(document["error"]["type"], "WorkflowConfigError")
            self.assertIn("ACTIVE_GERRIT_HOME", document["error"]["hint"])
            checks = {entry["name"]: entry for entry in document["checks"]}
            self.assertEqual(checks["active_gerrit_cli"]["status"], "failed")
            self.assertIn("Could not find active-gerrit/scripts/gerrit_cli.py", checks["active_gerrit_cli"]["evidence"][0])

    def test_doctor_wraps_active_gerrit_failure_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_active_gerrit_stub(
                root,
                """
                import json
                import sys

                document = {
                    "ok": False,
                    "command": "doctor",
                    "source": "gerrit",
                    "data": None,
                    "warnings": [],
                    "error": {
                        "type": "AuthenticationError",
                        "message": "bad credentials",
                        "hint": "Refresh the Gerrit HTTP password.",
                        "status": 401,
                    },
                    "meta": {"fetched_at": "2026-05-08T10:00:00+00:00"},
                }
                print(json.dumps(document, sort_keys=True))
                raise SystemExit(1)
                """,
            )
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "ACTIVE_GERRIT_HOME": str(root / "active-gerrit"),
            }

            completed = self.run_cli("doctor", env=env)

            self.assertEqual(completed.returncode, 1, completed.stderr)
            document = json.loads(completed.stdout)
            self.assertFalse(document["ok"])
            self.assertEqual(document["used_active_gerrit_commands"], ["doctor"])
            self.assertEqual(document["error"]["type"], "AuthenticationError")
            self.assertEqual(document["error"]["status"], 401)
            self.assertIn("Refresh the Gerrit HTTP password.", document["next_actions"])
            checks = {entry["name"]: entry for entry in document["checks"]}
            self.assertEqual(checks["active_gerrit_doctor"]["status"], "failed")
            self.assertIn("bad credentials", checks["active_gerrit_doctor"]["evidence"][0])

    def test_my_review_queue_sorts_oldest_first_and_marks_triage_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_active_gerrit_stub(
                root,
                """
                import json
                import sys

                args = sys.argv[1:]
                if "query-preset" in args:
                    document = {
                        "ok": True,
                        "command": "query-preset",
                        "source": "gerrit",
                        "data": [
                                {
                                    "id": "proj~1002",
                                    "triplet_id": "proj~master~I1002",
                                    "number": 1002,
                                    "project": "proj",
                                    "branch": "release-1.2",
                                    "change_id": "I1002",
                                    "subject": "Release branch fix",
                                    "status": "NEW",
                                    "owner": {"username": "bob", "name": "Bob"},
                                    "updated": "2026-05-02 09:00:00.000000000",
                                    "labels": {"Code-Review": {}},
                                    "submit_requirements": [],
                                    "unresolved_comment_count": 2,
                                    "hashtags": [],
                                    "topic": "release-fix",
                                    "is_private": False,
                                    "work_in_progress": False,
                                    "reviewed": True,
                                },
                                {
                                    "id": "proj~1004",
                                    "triplet_id": "proj~master~I1004",
                                    "number": 1004,
                                    "project": "proj",
                                    "branch": "master",
                                    "change_id": "I1004",
                                    "subject": "WIP cleanup",
                                    "status": "NEW",
                                    "owner": {"username": "dana", "name": "Dana"},
                                    "updated": "2026-05-04 08:00:00.000000000",
                                    "labels": {},
                                    "submit_requirements": [],
                                    "unresolved_comment_count": 0,
                                    "hashtags": [],
                                    "topic": "cleanup",
                                    "is_private": False,
                                    "work_in_progress": True,
                                    "reviewed": True,
                                },
                                {
                                    "id": "proj~1001",
                                    "triplet_id": "proj~master~I1001",
                                    "number": 1001,
                                    "project": "proj",
                                    "branch": "master",
                                    "change_id": "I1001",
                                    "subject": "Parser refactor",
                                    "status": "NEW",
                                    "owner": {"username": "alice", "name": "Alice"},
                                    "updated": "2026-05-01 10:00:00.000000000",
                                    "labels": {"Code-Review": {}},
                                    "submit_requirements": [],
                                    "unresolved_comment_count": 0,
                                    "hashtags": ["parser"],
                                    "topic": "parser",
                                    "is_private": False,
                                    "work_in_progress": False,
                                    "reviewed": False,
                                },
                                {
                                    "id": "proj~1003",
                                    "triplet_id": "proj~master~I1003",
                                    "number": 1003,
                                    "project": "proj",
                                    "branch": "master",
                                    "change_id": "I1003",
                                    "subject": "Security fix",
                                    "status": "NEW",
                                    "owner": {"username": "carol", "name": "Carol"},
                                    "updated": "2026-05-03 11:00:00.000000000",
                                    "labels": {"Verified": {}},
                                    "submit_requirements": [],
                                    "unresolved_comment_count": 1,
                                    "hashtags": ["security"],
                                    "topic": "security",
                                    "is_private": True,
                                    "work_in_progress": False,
                                    "reviewed": False,
                                },
                            ],
                        "warnings": [],
                        "meta": {"fetched_at": "2026-05-08T10:00:00+00:00"},
                    }
                    print(json.dumps(document, sort_keys=True))
                    raise SystemExit(0)

                document = {
                    "ok": False,
                    "command": "unknown",
                    "source": "gerrit",
                    "data": None,
                    "warnings": [],
                    "error": {"type": "ValidationError", "message": "unexpected command"},
                    "meta": {"fetched_at": "2026-05-08T10:00:00+00:00"},
                }
                print(json.dumps(document, sort_keys=True))
                raise SystemExit(1)
                """,
            )
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "ACTIVE_GERRIT_HOME": str(root),
            }

            completed = self.run_cli("my-review-queue", "--limit", "4", env=env)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            document = json.loads(completed.stdout)
            self.assertTrue(document["ok"])
            self.assertEqual(document["workflow"], "my-review-queue")
            self.assertEqual(document["used_active_gerrit_commands"], ["query-preset"])
            self.assertEqual(document["decision"]["status"], "warning")
            self.assertEqual(document["target"]["preset"], "my_open_reviews")
            self.assertEqual(document["target"]["limit"], 4)
            self.assertEqual(document["queue"]["sort_order"], "updated_oldest_first")
            self.assertEqual(document["queue"]["summary"]["total_changes"], 4)
            self.assertEqual(document["queue"]["summary"]["needs_my_response_changes"], 2)
            self.assertEqual(document["queue"]["summary"]["changes_with_unresolved_comments"], 2)
            self.assertEqual(document["queue"]["summary"]["private_changes"], 1)
            self.assertEqual(document["queue"]["summary"]["work_in_progress_changes"], 1)
            changes = document["queue"]["changes"]
            self.assertEqual([change["change"] for change in changes], ["proj~1001", "proj~1002", "proj~1003", "proj~1004"])
            self.assertEqual(changes[0]["next_action"], "inspect_latest_patch_set_and_respond")
            self.assertTrue(changes[0]["flags"]["needs_my_response"])
            self.assertEqual(changes[1]["next_action"], "ask_owner_for_clarification_on_unresolved_threads")
            self.assertTrue(changes[1]["flags"]["release_branch"])
            self.assertEqual(changes[2]["next_action"], "confirm_private_context_before_reviewing")
            self.assertTrue(changes[2]["flags"]["is_private"])
            self.assertEqual(changes[3]["next_action"], "skip_wip_until_owner_marks_ready")
            checks = {entry["name"]: entry for entry in document["checks"]}
            self.assertEqual(checks["review_queue_query"]["status"], "passed")
            self.assertEqual(
                checks["review_queue_query"]["details"]["invocation"],
                ["query-preset", "my_open_reviews", "--limit", "4", "--option", "REVIEWED"],
            )
            self.assertIn("Start with changes where Gerrit reports your response is still missing.", document["next_actions"])

    def test_review_brief_fetches_change_files_and_selected_diffs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_active_gerrit_stub(
                root,
                """
                import json
                import sys

                args = sys.argv[1:]
                if args == ["get-change", "--change", "proj~4247", "--detail", "full"]:
                    document = {
                        "ok": True,
                        "command": "get-change",
                        "source": "gerrit",
                        "data": {
                            "summary": {
                                "id": "proj~4247",
                                "number": 4247,
                                "project": "proj",
                                "branch": "master",
                                "subject": "Harden auth path",
                                "status": "NEW",
                                "owner": {"username": "alice", "name": "Alice"},
                                "updated": "2026-05-09 08:00:00.000000000",
                                "current_patch_set": 7,
                                "topic": "auth-hardening",
                                "hashtags": ["security"],
                                "work_in_progress": False,
                                "is_private": False,
                                "unresolved_comment_count": 2,
                            },
                            "revisions": [],
                            "reviewers": {"REVIEWER": [], "CC": [], "REMOVED": []},
                            "messages": [
                                {"id": "m1", "message": "Please add tests."},
                                {"id": "m2", "message": "Updated for auth path."},
                            ],
                            "reviewer_updates": [],
                            "actions": {},
                            "raw": None,
                        },
                        "warnings": [],
                        "meta": {"fetched_at": "2026-05-09T08:00:00+00:00"},
                    }
                    print(json.dumps(document, sort_keys=True))
                    raise SystemExit(0)

                if args == ["list-files", "--change", "proj~4247", "--revision", "current"]:
                    document = {
                        "ok": True,
                        "command": "list-files",
                        "source": "gerrit",
                        "data": {
                            "change": "proj~4247",
                            "requested_revision": "current",
                            "revision": "7",
                            "revision_sha": "deadbeef",
                            "patch_set": 7,
                            "files": [
                                {
                                    "file": "src/security/auth.py",
                                    "status": "M",
                                    "old_path": None,
                                    "lines_inserted": 80,
                                    "lines_deleted": 20,
                                    "size_delta": 180,
                                    "size": 4096,
                                    "old_mode": None,
                                    "new_mode": None,
                                },
                                {
                                    "file": "build/release.yaml",
                                    "status": "M",
                                    "old_path": None,
                                    "lines_inserted": 12,
                                    "lines_deleted": 4,
                                    "size_delta": 20,
                                    "size": 512,
                                    "old_mode": None,
                                    "new_mode": None,
                                },
                                {
                                    "file": "generated/api/client.pb.go",
                                    "status": "M",
                                    "old_path": None,
                                    "lines_inserted": 60,
                                    "lines_deleted": 10,
                                    "size_delta": 400,
                                    "size": 8192,
                                    "old_mode": None,
                                    "new_mode": None,
                                },
                                {
                                    "file": "tests/test_auth.py",
                                    "status": "A",
                                    "old_path": None,
                                    "lines_inserted": 25,
                                    "lines_deleted": 0,
                                    "size_delta": 140,
                                    "size": 1024,
                                    "old_mode": None,
                                    "new_mode": None,
                                },
                            ],
                        },
                        "warnings": [],
                        "meta": {"fetched_at": "2026-05-09T08:00:01+00:00"},
                    }
                    print(json.dumps(document, sort_keys=True))
                    raise SystemExit(0)

                if args[:6] == ["get-diff", "--change", "proj~4247", "--revision", "current", "--file"]:
                    file_path = args[6]
                    document = {
                        "ok": True,
                        "command": "get-diff",
                        "source": "gerrit",
                        "data": {
                            "change": "proj~4247",
                            "revision": "7",
                            "requested_revision": "current",
                            "revision_sha": "deadbeef",
                            "patch_set": 7,
                            "base": "6",
                            "file": file_path,
                            "change_type": "MODIFIED",
                            "meta_a": {"name": file_path, "content_type": "text/plain"},
                            "meta_b": {"name": file_path, "content_type": "text/plain"},
                            "content": [
                                {"ab": [" context line"]},
                                {"a": ["- old line"], "b": ["+ new line"]},
                            ],
                            "diff_header": [
                                f"diff --git a/{file_path} b/{file_path}",
                                "index 123..456 100644",
                            ],
                            "intraline_status": "OK",
                            "web_links": [],
                            "warnings": [],
                        },
                        "warnings": [],
                        "meta": {"fetched_at": "2026-05-09T08:00:02+00:00"},
                    }
                    print(json.dumps(document, sort_keys=True))
                    raise SystemExit(0)

                document = {
                    "ok": False,
                    "command": "unknown",
                    "source": "gerrit",
                    "data": None,
                    "warnings": [],
                    "error": {"type": "ValidationError", "message": "unexpected command"},
                    "meta": {"fetched_at": "2026-05-09T08:00:03+00:00"},
                }
                print(json.dumps(document, sort_keys=True))
                raise SystemExit(1)
                """,
            )
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "ACTIVE_GERRIT_HOME": str(root),
            }

            completed = self.run_cli(
                "review-brief",
                "--change",
                "proj~4247",
                "--max-diff-files",
                "3",
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            document = json.loads(completed.stdout)
            self.assertTrue(document["ok"])
            self.assertEqual(document["workflow"], "review-brief")
            self.assertEqual(
                document["used_active_gerrit_commands"],
                ["get-change", "list-files", "get-diff", "get-diff", "get-diff"],
            )
            self.assertEqual(document["decision"]["status"], "warning")
            self.assertEqual(document["target"]["change"], "proj~4247")
            self.assertEqual(document["target"]["revision"], "current")
            self.assertIn("Harden auth path touches 4 files", document["decision"]["summary"])
            brief = document["brief"]
            self.assertEqual(brief["change"]["id"], "proj~4247")
            self.assertEqual(brief["changed_file_overview"]["files_changed"], 4)
            self.assertEqual(brief["changed_file_overview"]["test_files_changed"], 1)
            self.assertEqual(brief["changed_file_overview"]["unresolved_comment_count"], 2)
            self.assertEqual(brief["review_order"], [
                "src/security/auth.py",
                "build/release.yaml",
                "generated/api/client.pb.go",
            ])
            self.assertEqual(brief["risk_areas"][0]["file"], "src/security/auth.py")
            self.assertIn("security_sensitive_path", brief["risk_areas"][0]["risk_reasons"])
            self.assertEqual(brief["files_to_inspect"][0]["diff"]["header_preview"][0], "diff --git a/src/security/auth.py b/src/security/auth.py")
            self.assertIn(
                "2 unresolved comment threads exist; confirm whether they still apply to the current patch set.",
                brief["open_questions"],
            )
            self.assertIn(
                "Inspect files in this order: src/security/auth.py, build/release.yaml, generated/api/client.pb.go.",
                document["next_actions"],
            )
            self.assertIn(
                "This workflow is report-only; publish comments or votes separately after manual inspection.",
                document["next_actions"],
            )
            checks = {entry["name"]: entry for entry in document["checks"]}
            self.assertEqual(checks["review_brief_get_change"]["status"], "passed")
            self.assertEqual(
                checks["review_brief_get_change"]["details"]["invocation"],
                ["get-change", "--change", "proj~4247", "--detail", "full"],
            )
            self.assertEqual(checks["review_brief_list_files"]["status"], "passed")
            self.assertEqual(
                checks["review_brief_list_files"]["details"]["invocation"],
                ["list-files", "--change", "proj~4247", "--revision", "current"],
            )
            self.assertEqual(checks["review_brief_diffs"]["status"], "passed")
            self.assertEqual(
                checks["review_brief_diffs"]["details"]["requested_files"],
                ["src/security/auth.py", "build/release.yaml", "generated/api/client.pb.go"],
            )


if __name__ == "__main__":
    unittest.main()