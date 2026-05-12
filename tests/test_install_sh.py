#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "install.sh"


class InstallScriptTests(unittest.TestCase):
    maxDiff = None

    def run_command(self, *args: str, env: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
        )

    def run_installer(
        self,
        *args: str,
        env: dict[str, str],
        script_path: Path | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script_path or INSTALLER_PATH), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            input=input_text,
            env=env,
            check=False,
        )

    def make_env(self, root: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(root / "xdg-data"),
                "XDG_CONFIG_HOME": str(root / "xdg-config"),
                "XDG_CACHE_HOME": str(root / "xdg-cache"),
                "XDG_STATE_HOME": str(root / "xdg-state"),
                "CODEX_HOME": str(root / "codex-home"),
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            }
        )
        return env

    def create_source_repo(
        self,
        root: Path,
        name: str,
        files: dict[str, str] | None = None,
    ) -> tuple[Path, str]:
        repo = root / name
        repo.mkdir(parents=True, exist_ok=True)

        self.run_command("git", "init", str(repo), env=os.environ.copy())
        self.run_command("git", "-C", str(repo), "checkout", "-B", "main", env=os.environ.copy())
        self.run_command("git", "-C", str(repo), "config", "user.name", "Installer Test", env=os.environ.copy())
        self.run_command("git", "-C", str(repo), "config", "user.email", "installer-test@example.com", env=os.environ.copy())

        repo_files = {"README.md": f"# {name}\n"}
        if files:
            repo_files.update(files)
        for relative_path, content in repo_files.items():
            file_path = repo / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(textwrap.dedent(content), encoding="utf-8")

        self.run_command("git", "-C", str(repo), "add", ".", env=os.environ.copy())
        commit = self.run_command(
            "git",
            "-C",
            str(repo),
            "commit",
            "-m",
            f"init {name}",
            env=os.environ.copy(),
        )
        self.assertEqual(commit.returncode, 0, commit.stderr)

        head = self.run_command("git", "-C", str(repo), "rev-parse", "HEAD", env=os.environ.copy())
        self.assertEqual(head.returncode, 0, head.stderr)
        return repo.resolve(), head.stdout.strip()

    def prepare_doctor_install(
        self,
        root: Path,
        *,
        gerrit_stub: str,
        workflow_stub: str,
        config_body: str | None = None,
    ) -> tuple[dict[str, str], Path, Path, Path]:
        env = self.make_env(root)
        repo, _ = self.create_source_repo(
            root,
            "doctor-source",
            files={
                "active-gerrit/scripts/gerrit_cli.py": gerrit_stub,
                "active-gerrit-workflow/scripts/workflow_cli.py": workflow_stub,
            },
        )
        install_dir = root / "xdg-data" / "active-gerrit-workflow"
        clone = self.run_command("git", "clone", str(repo), str(install_dir), env=os.environ.copy())
        self.assertEqual(clone.returncode, 0, clone.stderr)

        config_dir = root / "xdg-config" / "active-gerrit-workflow"
        cache_dir = root / "xdg-cache" / "active-gerrit-workflow"
        state_dir = root / "xdg-state" / "active-gerrit-workflow"
        config_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "env"
        config_file.write_text(
            textwrap.dedent(
                config_body
                or """
                export TEST_CONFIG_LOADED="1"
                export GERRIT_BASE_URL="https://gerrit.example.com"
                export GERRIT_USERNAME="alice"
                export GERRIT_HTTP_PASSWORD="secret-token"
                """
            ).lstrip(),
            encoding="utf-8",
        )
        return env, repo, install_dir, config_file

    def make_controlled_path(
        self,
        root: Path,
        *,
        extra_scripts: dict[str, str] | None = None,
        include_optional: bool = False,
    ) -> str:
        bin_dir = root / "controlled-bin"
        bin_dir.mkdir(parents=True, exist_ok=True)

        base_commands = [
            "bash",
            "git",
            "curl",
            "sed",
            "dirname",
            "basename",
            "mkdir",
            "mktemp",
            "mv",
            "chmod",
            "rm",
            "stat",
            "grep",
            "cat",
            "date",
            "python3",
        ]
        optional_commands = ["jq", "openssl", "ssh", "rg", "shellcheck", "bats", "wget"]
        commands = base_commands + (optional_commands if include_optional else [])

        for command_name in commands:
            source = shutil.which(command_name)
            if source is None:
                continue
            target = bin_dir / command_name
            if target.exists():
                target.unlink()
            target.symlink_to(source)

        for script_name, script_body in (extra_scripts or {}).items():
            target = bin_dir / script_name
            if target.exists() or target.is_symlink():
                target.unlink()
            target.write_text(textwrap.dedent(script_body).lstrip(), encoding="utf-8")
            target.chmod(0o755)

        return str(bin_dir)

    def read_install_state(self, state_file: Path) -> dict[str, str]:
        command = (
            f'source "{state_file}" && '
            'printf "STATE_INSTALL_DIR=%s\n" "$STATE_INSTALL_DIR" && '
            'printf "STATE_CONFIG_FILE=%s\n" "$STATE_CONFIG_FILE" && '
            'printf "STATE_SKILL_DIR=%s\n" "$STATE_SKILL_DIR" && '
            'printf "STATE_SKILL_MODE=%s\n" "$STATE_SKILL_MODE" && '
            'printf "STATE_REPO_URL=%s\n" "$STATE_REPO_URL" && '
            'printf "STATE_REF=%s\n" "$STATE_REF" && '
            'printf "STATE_INSTALLED_COMMIT=%s\n" "$STATE_INSTALLED_COMMIT" && '
            'printf "STATE_INSTALLED_AT=%s\n" "$STATE_INSTALLED_AT"'
        )
        completed = self.run_command("bash", "-c", command, env=os.environ.copy())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        state: dict[str, str] = {}
        for line in completed.stdout.strip().splitlines():
            key, value = line.split("=", 1)
            state[key] = value
        return state

    def test_install_clones_from_standalone_script_and_records_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            repo, commit = self.create_source_repo(root, "source-repo")
            standalone_installer = root / "downloaded-install.sh"
            shutil.copy2(INSTALLER_PATH, standalone_installer)

            completed = self.run_installer(
                "install",
                "--repo-url",
                str(repo),
                "--ref",
                "main",
                env=env,
                script_path=standalone_installer,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            install_dir = root / "xdg-data" / "active-gerrit-workflow"
            self.assertTrue((install_dir / ".git").exists())

            state_file = root / "xdg-config" / "active-gerrit-workflow" / "install-state"
            state = self.read_install_state(state_file)
            self.assertEqual(state["STATE_REPO_URL"], str(repo))
            self.assertEqual(state["STATE_REF"], "main")
            self.assertEqual(state["STATE_INSTALLED_COMMIT"], commit)
            self.assertEqual(state["STATE_INSTALL_DIR"], str(install_dir))
            self.assertTrue(state["STATE_INSTALLED_AT"])

    def test_repeated_install_reuses_existing_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            repo, commit = self.create_source_repo(root, "repeat-source")

            first = self.run_installer("install", "--repo-url", str(repo), "--ref", "main", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)

            second = self.run_installer("install", "--repo-url", str(repo), "--ref", "main", env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already contains the expected repository", second.stdout)

            state_file = root / "xdg-config" / "active-gerrit-workflow" / "install-state"
            state = self.read_install_state(state_file)
            self.assertEqual(state["STATE_INSTALLED_COMMIT"], commit)

    def test_install_refuses_to_overwrite_non_repo_directory_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            repo, _ = self.create_source_repo(root, "conflict-source")
            install_dir = root / "xdg-data" / "active-gerrit-workflow"
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "user-file.txt").write_text("keep me\n", encoding="utf-8")

            completed = self.run_installer("install", "--repo-url", str(repo), "--ref", "main", env=env)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Re-run with `--force`", completed.stderr)
            self.assertTrue((install_dir / "user-file.txt").exists())
            self.assertFalse((install_dir / ".git").exists())

    def test_install_force_backs_up_conflict_and_reclones(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            repo, commit = self.create_source_repo(root, "force-source")
            install_dir = root / "xdg-data" / "active-gerrit-workflow"
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "old.txt").write_text("old contents\n", encoding="utf-8")

            completed = self.run_installer(
                "install",
                "--repo-url",
                str(repo),
                "--ref",
                "main",
                "--force",
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((install_dir / ".git").exists())
            backups = sorted(root.glob("xdg-data/active-gerrit-workflow.bak.*"))
            self.assertTrue(backups)
            self.assertTrue((backups[0] / "old.txt").exists())

            state_file = root / "xdg-config" / "active-gerrit-workflow" / "install-state"
            state = self.read_install_state(state_file)
            self.assertEqual(state["STATE_INSTALLED_COMMIT"], commit)

    def test_install_fails_when_existing_origin_does_not_match_expected_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            repo_one, _ = self.create_source_repo(root, "repo-one")
            repo_two, _ = self.create_source_repo(root, "repo-two")

            first = self.run_installer("install", "--repo-url", str(repo_one), "--ref", "main", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)

            second = self.run_installer("install", "--repo-url", str(repo_two), "--ref", "main", env=env)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("different repository origin", second.stderr)

    def test_doctor_json_runs_both_python_doctors_with_loaded_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env, repo, install_dir, config_file = self.prepare_doctor_install(
                root,
                gerrit_stub="""
                import json
                import os

                print(json.dumps({
                    "ok": True,
                    "command": "doctor",
                    "source": "gerrit",
                    "data": {
                        "env_loaded": os.environ.get("TEST_CONFIG_LOADED"),
                    },
                    "warnings": [],
                }, sort_keys=True))
                """,
                workflow_stub="""
                import json
                import os

                print(json.dumps({
                    "ok": True,
                    "command": "doctor",
                    "source": "workflow",
                    "data": {
                        "active_gerrit_home": os.environ.get("ACTIVE_GERRIT_HOME"),
                    },
                    "warnings": [],
                }, sort_keys=True))
                """,
            )

            completed = self.run_installer(
                "doctor",
                "--json",
                "--repo-url",
                str(repo),
                "--install-dir",
                str(install_dir),
                "--config-file",
                str(config_file),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["data"]["python_doctors"]["active_gerrit"]["details"]["data"]["env_loaded"],
                "1",
            )
            self.assertEqual(
                payload["data"]["python_doctors"]["workflow"]["details"]["data"]["active_gerrit_home"],
                str(install_dir / "active-gerrit"),
            )

    def test_doctor_optional_dependencies_only_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env, repo, install_dir, config_file = self.prepare_doctor_install(
                root,
                gerrit_stub='print("{\\"ok\\": true, \\"command\\": \\"doctor\\", \\"source\\": \\"gerrit\\", \\"data\\": {}, \\"warnings\\": []}")\n',
                workflow_stub='print("{\\"ok\\": true, \\"command\\": \\"doctor\\", \\"source\\": \\"workflow\\", \\"data\\": {}, \\"warnings\\": []}")\n',
            )
            env["PATH"] = self.make_controlled_path(root)

            completed = self.run_installer(
                "doctor",
                "--json",
                "--repo-url",
                str(repo),
                "--install-dir",
                str(install_dir),
                "--config-file",
                str(config_file),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertIn("dependencies.jq", "\n".join(payload["warnings"]))
            self.assertIn("dependencies.rg", "\n".join(payload["warnings"]))

    def test_doctor_reports_python_version_failure_with_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env, repo, install_dir, config_file = self.prepare_doctor_install(
                root,
                gerrit_stub='print("{\\"ok\\": true, \\"command\\": \\"doctor\\", \\"source\\": \\"gerrit\\", \\"data\\": {}, \\"warnings\\": []}")\n',
                workflow_stub='print("{\\"ok\\": true, \\"command\\": \\"doctor\\", \\"source\\": \\"workflow\\", \\"data\\": {}, \\"warnings\\": []}")\n',
            )
            env["PATH"] = self.make_controlled_path(
                root,
                extra_scripts={
                    "python3": """
                    #!/usr/bin/env bash
                    echo "3.8.18"
                    exit 1
                    """
                },
            )

            completed = self.run_installer(
                "doctor",
                "--repo-url",
                str(repo),
                "--install-dir",
                str(install_dir),
                "--config-file",
                str(config_file),
                env=env,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Python 3.9+ is required.", completed.stdout)
            self.assertIn("Install with", completed.stdout)

    def test_doctor_redacts_secrets_from_python_doctor_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env, repo, install_dir, config_file = self.prepare_doctor_install(
                root,
                gerrit_stub="""
                import json
                import os
                import sys

                print(json.dumps({
                    "ok": False,
                    "command": "doctor",
                    "source": "gerrit",
                    "error": {
                        "type": "AuthenticationError",
                        "message": f"token was {os.environ.get('GERRIT_HTTP_PASSWORD')}",
                        "hint": "Refresh the Gerrit HTTP password.",
                    },
                    "warnings": [],
                }, sort_keys=True))
                raise SystemExit(1)
                """,
                workflow_stub='print("{\\"ok\\": true, \\"command\\": \\"doctor\\", \\"source\\": \\"workflow\\", \\"data\\": {}, \\"warnings\\": []}")\n',
            )

            completed = self.run_installer(
                "doctor",
                "--json",
                "--repo-url",
                str(repo),
                "--install-dir",
                str(install_dir),
                "--config-file",
                str(config_file),
                env=env,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("secret-token", completed.stdout)
            self.assertIn("<redacted>", completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(
                payload["data"]["python_doctors"]["active_gerrit"]["summary"],
                "token was <redacted>",
            )

    def test_config_interactive_writes_sourceable_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            install_dir = root / "xdg-data" / "active-gerrit-workflow"
            install_dir.mkdir(parents=True, exist_ok=True)

            completed = self.run_installer(
                "config",
                "--install-dir",
                str(install_dir),
                env=env,
                input_text="\n".join(
                    [
                        "https://gerrit.example.com",
                        "alice",
                        "yes",
                        "top-secret",
                        "true",
                        "45",
                        "OWNER_REVIEWERS",
                        str(root / "xdg-cache" / "active-gerrit-workflow" / "gerrit-custom"),
                    ]
                )
                + "\n",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("GERRIT_HTTP_PASSWORD=<redacted>", completed.stdout)
            self.assertNotIn("top-secret", completed.stdout)

            config_file = root / "xdg-config" / "active-gerrit-workflow" / "env"
            sourced = self.run_command(
                "bash",
                "-c",
                f'source "{config_file}" && printf "%s\\n%s\\n%s\\n%s\\n" "$GERRIT_BASE_URL" "$GERRIT_USERNAME" "$GERRIT_TIMEOUT_SECONDS" "$GERRIT_HTTP_PASSWORD"',
                env=os.environ.copy(),
            )
            self.assertEqual(sourced.returncode, 0, sourced.stderr)
            self.assertEqual(
                sourced.stdout.splitlines(),
                [
                    "https://gerrit.example.com",
                    "alice",
                    "45",
                    "top-secret",
                ],
            )

    def test_config_reuses_existing_values_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            config_dir = root / "xdg-config" / "active-gerrit-workflow"
            install_dir = root / "xdg-data" / "active-gerrit-workflow"
            config_dir.mkdir(parents=True, exist_ok=True)
            install_dir.mkdir(parents=True, exist_ok=True)
            config_file = config_dir / "env"
            config_file.write_text(
                textwrap.dedent(
                    """
                    export GERRIT_BASE_URL="https://old.example.com"
                    export GERRIT_AUTH_TYPE="basic"
                    export GERRIT_USERNAME="old-user"
                    export GERRIT_HTTP_PASSWORD="old-secret"
                    export GERRIT_VERIFY_SSL="false"
                    export GERRIT_TIMEOUT_SECONDS="20"
                    export GERRIT_DEFAULT_NOTIFY="OWNER"
                    export GERRIT_CACHE_DIR="/tmp/old-cache"
                    """
                ).lstrip(),
                encoding="utf-8",
            )

            completed = self.run_installer(
                "config",
                "--install-dir",
                str(install_dir),
                env=env,
                input_text="\n".join(
                    [
                        "",
                        "",
                        "no",
                        "",
                        "",
                        "",
                    ]
                )
                + "\n",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            backups = sorted(config_dir.glob("env.bak.*"))
            self.assertTrue(backups)
            config_text = config_file.read_text(encoding="utf-8")
            self.assertIn('export GERRIT_BASE_URL=https://old.example.com', config_text)
            self.assertIn('export GERRIT_USERNAME=old-user', config_text)
            self.assertNotIn('export GERRIT_HTTP_PASSWORD=', config_text)

    def test_config_noninteractive_uses_environment_without_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            install_dir = root / "xdg-data" / "active-gerrit-workflow"
            install_dir.mkdir(parents=True, exist_ok=True)
            env.update(
                {
                    "NONINTERACTIVE": "1",
                    "GERRIT_BASE_URL": "https://gerrit.example.com",
                    "GERRIT_USERNAME": "ci-user",
                    "GERRIT_HTTP_PASSWORD": "ci-secret",
                    "GERRIT_TIMEOUT_SECONDS": "60",
                }
            )

            completed = self.run_installer(
                "config",
                "--install-dir",
                str(install_dir),
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn("ci-secret", completed.stdout)
            config_file = root / "xdg-config" / "active-gerrit-workflow" / "env"
            config_text = config_file.read_text(encoding="utf-8")
            self.assertIn('export GERRIT_USERNAME=ci-user', config_text)
            self.assertIn('export GERRIT_HTTP_PASSWORD=ci-secret', config_text)

    def test_config_noninteractive_requires_base_url_and_username(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = self.make_env(root)
            install_dir = root / "xdg-data" / "active-gerrit-workflow"
            install_dir.mkdir(parents=True, exist_ok=True)
            env["NONINTERACTIVE"] = "1"

            completed = self.run_installer(
                "config",
                "--install-dir",
                str(install_dir),
                env=env,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("NONINTERACTIVE=1 requires GERRIT_BASE_URL", completed.stderr)


if __name__ == "__main__":
    unittest.main()
