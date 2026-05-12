#!/usr/bin/env python3

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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
    ) -> subprocess.CompletedProcess[str]:
        return self.run_command("bash", str(script_path or INSTALLER_PATH), *args, env=env)

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

    def create_source_repo(self, root: Path, name: str) -> tuple[Path, str]:
        repo = root / name
        repo.mkdir(parents=True, exist_ok=True)

        self.run_command("git", "init", str(repo), env=os.environ.copy())
        self.run_command("git", "-C", str(repo), "checkout", "-B", "main", env=os.environ.copy())
        self.run_command("git", "-C", str(repo), "config", "user.name", "Installer Test", env=os.environ.copy())
        self.run_command("git", "-C", str(repo), "config", "user.email", "installer-test@example.com", env=os.environ.copy())

        (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        self.run_command("git", "-C", str(repo), "add", "README.md", env=os.environ.copy())
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


if __name__ == "__main__":
    unittest.main()
