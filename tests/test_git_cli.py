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

    def run_git_input(self, repo, *args, input_text):
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
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

    def make_repo(self, with_commit=True, *, initial_message="initial", files=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repo = Path(temp_dir.name)
        self.run_git(repo, "init", "-q")
        self.run_git(repo, "config", "user.name", "Tester")
        self.run_git(repo, "config", "user.email", "tester@example.com")
        if with_commit:
            seed_files = files or {"tracked.txt": "initial\n"}
            for path, content in seed_files.items():
                (repo / path).write_text(content, encoding="utf-8")
            self.run_git(repo, "add", "--", *seed_files.keys())
            if "\n" in initial_message:
                self.run_git_input(repo, "commit", "-q", "-F", "-", input_text=initial_message)
            else:
                self.run_git(repo, "commit", "-q", "-m", initial_message)
        return repo

    def head_message(self, repo):
        return self.run_git(repo, "show", "-s", "--format=%B", "HEAD").stdout

    def make_repo_with_remote(self):
        repo = self.make_repo()
        remote_dir = tempfile.TemporaryDirectory()
        self.addCleanup(remote_dir.cleanup)
        remote = Path(remote_dir.name) / "origin.git"
        self.run_git(Path(remote_dir.name), "init", "--bare", str(remote))
        self.run_git(repo, "remote", "add", "origin", str(remote))
        branch = self.run_git(repo, "branch", "--show-current").stdout.strip()
        self.run_git(repo, "push", "-u", "origin", branch)
        return repo, remote

    def install_fake_gerrit_cli(self, *, change="demo~4247", project="demo", branch="master", ref="refs/changes/47/4247/3"):
        fake_root = tempfile.TemporaryDirectory()
        self.addCleanup(fake_root.cleanup)
        scripts_dir = Path(fake_root.name) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / "gerrit_cli.py"
        script_path.write_text(
            """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if not args:
    print(json.dumps({"ok": False, "error": {"message": "missing command"}}))
    raise SystemExit(1)

while args and args[0].startswith("--"):
    if len(args) >= 2 and not args[1].startswith("--"):
        args = args[2:]
    else:
        args = args[1:]

if not args:
    print(json.dumps({"ok": False, "error": {"message": "missing command"}}))
    raise SystemExit(1)

command = args[0]
if command != "get-change":
    print(json.dumps({"ok": False, "error": {"message": f"unsupported command: {command}"}}))
    raise SystemExit(1)

def value_for(flag, default=None):
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default

change = value_for("--change", "__CHANGE__")
if change != "__CHANGE__":
    print(json.dumps({"ok": False, "error": {"message": f"unexpected change: {change}"}}))
    raise SystemExit(1)

payload = {
    "ok": True,
    "command": "get-change",
    "source": "gerrit",
    "data": {
        "summary": {
            "number": 4247,
            "project": "__PROJECT__",
            "branch": "__BRANCH__",
            "current_revision": "deadbeef",
            "current_patch_set": 3,
        },
        "revisions": [
            {
                "revision": "deadbeef",
                "patch_set": 3,
                "ref": "__REF__",
            }
        ],
        "reviewers": {},
        "messages": [],
        "reviewer_updates": [],
        "actions": {},
        "raw": None,
    },
    "warnings": [],
    "meta": {},
}
print(json.dumps(payload))
"""
            .replace("__CHANGE__", change)
            .replace("__PROJECT__", project)
            .replace("__BRANCH__", branch)
            .replace("__REF__", ref),
            encoding="utf-8",
        )
        script_path.chmod(0o755)
        return Path(fake_root.name)

    def publish_patchset_ref(self, repo, ref="refs/changes/47/4247/3"):
        (repo / "patchset.txt").write_text("patchset\n", encoding="utf-8")
        self.run_git(repo, "add", "patchset.txt")
        self.run_git(repo, "commit", "-q", "-m", "patchset")
        commit = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        self.run_git(repo, "push", "origin", f"HEAD:{ref}")
        return commit

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

    def test_fetch_change_fetches_gerrit_patchset_ref(self) -> None:
        repo, _remote = self.make_repo_with_remote()
        expected_commit = self.publish_patchset_ref(repo)
        fake_home = self.install_fake_gerrit_cli()

        env = dict(os.environ)
        env["ACTIVE_GERRIT_HOME"] = str(fake_home)
        result = self.run_cli("--repo", str(repo), "fetch-change", "--change", "demo~4247", "--remote", "origin", env=env)

        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "fetch-change")
        self.assertEqual(payload["data"]["ref"], "refs/changes/47/4247/3")
        self.assertEqual(payload["data"]["remote"], "origin")
        self.assertEqual(payload["data"]["fetched_commit"], expected_commit)

    def test_checkout_change_rejects_dirty_worktree_by_default(self) -> None:
        repo, _remote = self.make_repo_with_remote()
        self.publish_patchset_ref(repo)
        fake_home = self.install_fake_gerrit_cli()
        (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")

        env = dict(os.environ)
        env["ACTIVE_GERRIT_HOME"] = str(fake_home)
        result = self.run_cli("--repo", str(repo), "checkout-change", "--change", "demo~4247", "--remote", "origin", env=env)

        self.assertEqual(result.returncode, 1)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "checkout-change")
        self.assertIn("worktree-change", payload["error"]["message"])

    def test_checkout_change_creates_default_review_branch(self) -> None:
        repo, _remote = self.make_repo_with_remote()
        expected_commit = self.publish_patchset_ref(repo)
        fake_home = self.install_fake_gerrit_cli()

        env = dict(os.environ)
        env["ACTIVE_GERRIT_HOME"] = str(fake_home)
        result = self.run_cli("--repo", str(repo), "checkout-change", "--change", "demo~4247", "--remote", "origin", env=env)

        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "checkout-change")
        self.assertEqual(payload["data"]["checkout_mode"], "branch")
        self.assertEqual(payload["data"]["branch"], "review/4247-3")
        self.assertEqual(self.run_git(repo, "branch", "--show-current").stdout.strip(), "review/4247-3")
        self.assertEqual(self.run_git(repo, "rev-parse", "HEAD").stdout.strip(), expected_commit)

    def test_worktree_change_creates_isolated_worktree(self) -> None:
        repo, _remote = self.make_repo_with_remote()
        expected_commit = self.publish_patchset_ref(repo)
        fake_home = self.install_fake_gerrit_cli()
        worktree_parent = tempfile.TemporaryDirectory()
        self.addCleanup(worktree_parent.cleanup)
        worktree_path = Path(worktree_parent.name) / "review-wt"
        original_branch = self.run_git(repo, "branch", "--show-current").stdout.strip()

        env = dict(os.environ)
        env["ACTIVE_GERRIT_HOME"] = str(fake_home)
        result = self.run_cli(
            "--repo",
            str(repo),
            "worktree-change",
            "--change",
            "demo~4247",
            "--remote",
            "origin",
            "--path",
            str(worktree_path),
            env=env,
        )

        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "worktree-change")
        self.assertTrue(worktree_path.exists())
        self.assertEqual(payload["data"]["worktree"]["path"], str(worktree_path))
        self.assertEqual(payload["data"]["worktree"]["head"], expected_commit)
        self.assertEqual(self.run_git(repo, "branch", "--show-current").stdout.strip(), original_branch)

    def test_change_id_check_reads_message_file(self) -> None:
        repo = self.make_repo()
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        message_file = Path(temp_dir.name) / "commit-message.txt"
        message_file.write_text("Fix bug\n\nChange-Id: Iabc1234\n", encoding="utf-8")

        result = self.run_cli("--repo", str(repo), "change-id-check", "--message-file", str(message_file))
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["status"], "present")
        self.assertEqual(payload["data"]["value"], "Iabc1234")
        self.assertEqual(payload["data"]["message_file"], str(message_file))

    def test_commit_plan_reports_message_summary_and_change_id(self) -> None:
        repo = self.make_repo(
            initial_message="Initial change\n\nChange-Id: Iabc1234\n",
            files={"tracked.txt": "initial\n", "other.txt": "other\n"},
        )
        (repo / "tracked.txt").write_text("initial\nupdated\n", encoding="utf-8")
        self.run_git(repo, "add", "tracked.txt")

        result = self.run_cli(
            "--repo",
            str(repo),
            "commit-plan",
            "--amend",
            "--message",
            "Refine change\n\nChange-Id: Iabc1234\n",
            "tracked.txt",
        )
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertEqual(payload["data"]["mode"], "amend")
        self.assertEqual(payload["data"]["message"]["change_id"]["value"], "Iabc1234")
        self.assertEqual(payload["data"]["message"]["summary"]["subject"], "Refine change")
        self.assertEqual(payload["data"]["path_scope"]["staged_paths"], ["tracked.txt"])

    def test_commit_create_requires_change_id_without_hook(self) -> None:
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("initial\nupdated\n", encoding="utf-8")

        result = self.run_cli(
            "--repo",
            str(repo),
            "commit-create",
            "--message",
            "Missing change id\n",
            "tracked.txt",
        )
        self.assertEqual(result.returncode, 2)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "ValidationError")
        self.assertIn("Change-Id", payload["error"]["message"])

    def test_commit_create_executes_with_yes_and_only_selected_paths(self) -> None:
        repo = self.make_repo(files={"tracked.txt": "initial\n", "other.txt": "other\n"})
        before_head = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "tracked.txt").write_text("initial\nselected\n", encoding="utf-8")
        (repo / "other.txt").write_text("other\nstaged-other\n", encoding="utf-8")
        self.run_git(repo, "add", "other.txt")
        message = 'Create selected commit\n\nBody with "quotes"\n\nChange-Id: Iabcd1234\n'

        plan_result = self.run_cli(
            "--repo",
            str(repo),
            "commit-create",
            "--message",
            message,
            "tracked.txt",
        )
        self.assertEqual(plan_result.returncode, 0)
        plan_payload = self.parse_stdout_json(plan_result)
        self.assertFalse(plan_payload["data"]["executed"])
        self.assertEqual(self.run_git(repo, "rev-parse", "HEAD").stdout.strip(), before_head)

        exec_result = self.run_cli(
            "--repo",
            str(repo),
            "--yes",
            "commit-create",
            "--message",
            message,
            "tracked.txt",
        )
        self.assertEqual(exec_result.returncode, 0)
        payload = self.parse_stdout_json(exec_result)
        self.assertTrue(payload["data"]["executed"])
        self.assertNotEqual(payload["data"]["head_after"]["commit"], before_head)
        changed_files = [line for line in self.run_git(repo, "show", "--pretty=format:", "--name-only", "HEAD").stdout.splitlines() if line]
        self.assertEqual(changed_files, ["tracked.txt"])
        cached_files = [line for line in self.run_git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if line]
        self.assertEqual(cached_files, ["other.txt"])
        self.assertEqual(self.head_message(repo).rstrip("\n"), message.rstrip("\n"))

    def test_commit_amend_rejects_change_id_change_by_default(self) -> None:
        repo = self.make_repo(initial_message="Initial change\n\nChange-Id: Iabc1234\n")
        (repo / "tracked.txt").write_text("initial\nupdated\n", encoding="utf-8")
        self.run_git(repo, "add", "tracked.txt")

        result = self.run_cli(
            "--repo",
            str(repo),
            "commit-amend",
            "--message",
            "Updated change\n\nChange-Id: Ideadbeef\n",
        )
        self.assertEqual(result.returncode, 2)
        payload = self.parse_stdout_json(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "ValidationError")
        self.assertIn("would change Change-Id", payload["error"]["message"])

    def test_commit_amend_executes_and_preserves_change_id(self) -> None:
        repo = self.make_repo(initial_message="Initial change\n\nChange-Id: Iabc1234\n")
        before_head = self.run_git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "tracked.txt").write_text("initial\nupdated\n", encoding="utf-8")
        self.run_git(repo, "add", "tracked.txt")
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        message_file = Path(temp_dir.name) / "amend-message.txt"
        message_file.write_text("Updated change\n\nKeep trailer\n\nChange-Id: Iabc1234\n", encoding="utf-8")

        result = self.run_cli(
            "--repo",
            str(repo),
            "--yes",
            "commit-amend",
            "--message-file",
            str(message_file),
        )
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertTrue(payload["data"]["executed"])
        self.assertNotEqual(payload["data"]["head_after"]["commit"], before_head)
        self.assertEqual(payload["data"]["head_after"]["change_id"]["value"], "Iabc1234")
        self.assertEqual(
            self.head_message(repo).rstrip("\n"),
            "Updated change\n\nKeep trailer\n\nChange-Id: Iabc1234\n".rstrip("\n"),
        )

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

    def test_repo_diff_default_excludes_patch(self) -> None:
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("initial\nchanged\n", encoding="utf-8")
        result = self.run_cli("--repo", str(repo), "repo-diff")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertIsNone(payload["data"]["patch"])
        self.assertFalse(payload["data"]["patch_truncated"])
        self.assertFalse(payload["data"]["staged"])
        self.assertEqual(payload["data"]["files"][0]["status"], "M")
        self.assertEqual(payload["data"]["stat"]["files_changed"], 1)

    def test_repo_diff_staged_identifies_rename_copy_and_delete_with_spaces(self) -> None:
        repo = self.make_repo(with_commit=False)
        (repo / "copy source.txt").write_text("copy\n", encoding="utf-8")
        (repo / "rename from.txt").write_text("rename\n", encoding="utf-8")
        (repo / "delete me.txt").write_text("delete\n", encoding="utf-8")
        self.run_git(repo, "add", "copy source.txt", "rename from.txt", "delete me.txt")
        self.run_git(repo, "commit", "-q", "-m", "seed")

        self.run_git(repo, "mv", "rename from.txt", "rename to.txt")
        (repo / "copy target.txt").write_text((repo / "copy source.txt").read_text(encoding="utf-8"), encoding="utf-8")
        self.run_git(repo, "add", "copy target.txt")
        self.run_git(repo, "rm", "delete me.txt")

        result = self.run_cli("--repo", str(repo), "repo-diff", "--staged")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        statuses = {entry["status"]: entry for entry in payload["data"]["files"]}
        self.assertIn("R", statuses)
        self.assertIn("C", statuses)
        self.assertIn("D", statuses)
        self.assertEqual(statuses["R"]["old_path"], "rename from.txt")
        self.assertEqual(statuses["R"]["path"], "rename to.txt")
        self.assertEqual(statuses["C"]["old_path"], "copy source.txt")
        self.assertEqual(statuses["C"]["path"], "copy target.txt")
        self.assertEqual(statuses["D"]["path"], "delete me.txt")

    def test_repo_diff_file_includes_patch_for_space_path(self) -> None:
        repo = self.make_repo(with_commit=False)
        (repo / "space file.txt").write_text("a\n", encoding="utf-8")
        self.run_git(repo, "add", "space file.txt")
        self.run_git(repo, "commit", "-q", "-m", "space")
        (repo / "space file.txt").write_text("a\nb\n", encoding="utf-8")

        result = self.run_cli("--repo", str(repo), "repo-diff-file", "space file.txt")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertEqual(payload["data"]["path"], "space file.txt")
        self.assertIn("diff --git a/space file.txt b/space file.txt", payload["data"]["patch"])

    def test_repo_diff_file_handles_path_starting_with_dash(self) -> None:
        repo = self.make_repo(with_commit=False)
        (repo / "-leading.txt").write_text("a\n", encoding="utf-8")
        self.run_git(repo, "add", "--", "-leading.txt")
        self.run_git(repo, "commit", "-q", "-m", "dash")
        (repo / "-leading.txt").write_text("a\nb\n", encoding="utf-8")

        result = self.run_cli("--repo", str(repo), "repo-diff-file", "--", "-leading.txt")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertEqual(payload["data"]["path"], "-leading.txt")
        self.assertEqual(payload["data"]["files"][0]["path"], "-leading.txt")

    def test_repo_log_returns_structured_commits(self) -> None:
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("initial\nnext\n", encoding="utf-8")
        self.run_git(repo, "commit", "-am", "second commit")

        result = self.run_cli("--repo", str(repo), "repo-log", "--limit", "2")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertEqual(len(payload["data"]["commits"]), 2)
        self.assertEqual(payload["data"]["commits"][0]["subject"], "second commit")
        self.assertTrue(payload["data"]["commits"][0]["parents"])

    def test_repo_show_returns_commit_summary_and_optional_patch(self) -> None:
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("initial\nnext\n", encoding="utf-8")
        self.run_git(repo, "commit", "-am", "second commit")

        result = self.run_cli("--repo", str(repo), "repo-show", "--include-patch")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        self.assertEqual(payload["data"]["subject"], "second commit")
        self.assertEqual(payload["data"]["files"][0]["path"], "tracked.txt")
        self.assertIn("diff --git a/tracked.txt b/tracked.txt", payload["data"]["patch"])

    def test_repo_branches_all_includes_remote_tracking_branches(self) -> None:
        repo, _remote = self.make_repo_with_remote()
        self.run_git(repo, "checkout", "-qb", "feature/demo")
        (repo / "tracked.txt").write_text("initial\nfeature\n", encoding="utf-8")
        self.run_git(repo, "commit", "-am", "feature branch")
        self.run_git(repo, "push", "-u", "origin", "feature/demo")

        result = self.run_cli("--repo", str(repo), "repo-branches", "--all")
        self.assertEqual(result.returncode, 0)
        payload = self.parse_stdout_json(result)
        names = {entry["name"]: entry for entry in payload["data"]["branches"]}
        self.assertIn("feature/demo", names)
        self.assertIn("origin/feature/demo", names)
        self.assertFalse(names["origin/feature/demo"]["current"])
        self.assertTrue(names["origin/feature/demo"]["remote"])


if __name__ == "__main__":
    unittest.main()
