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


if __name__ == "__main__":
    unittest.main()