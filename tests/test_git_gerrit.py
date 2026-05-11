#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from git_gerrit import (  # noqa: E402
    build_change_ref,
    build_review_ref,
    normalize_review_ref_option_items,
    remote_matches_project,
    remote_url_matches_base,
    resolve_change_ref,
    select_gerrit_remote,
)


class GitGerritTests(unittest.TestCase):
    def test_build_change_ref_formats_refs_changes_path(self) -> None:
        self.assertEqual(build_change_ref(4247, 3), "refs/changes/47/4247/3")

    def test_resolve_change_ref_prefers_rest_ref_from_normalized_change(self) -> None:
        change = {
            "summary": {
                "number": 4247,
                "current_revision": "deadbeef",
                "current_patch_set": 3,
            },
            "revisions": [
                {
                    "revision": "deadbeef",
                    "patch_set": 3,
                    "ref": "refs/changes/47/4247/3",
                }
            ],
        }

        resolved = resolve_change_ref(change)
        self.assertEqual(resolved["ref"], "refs/changes/47/4247/3")
        self.assertEqual(resolved["source"], "rest")
        self.assertEqual(resolved["patch_set"], 3)

    def test_resolve_change_ref_falls_back_to_computed_ref(self) -> None:
        change = {
            "summary": {
                "number": 4247,
                "current_patch_set": 3,
            },
            "revisions": [
                {
                    "revision": "deadbeef",
                    "patch_set": 3,
                    "ref": None,
                }
            ],
        }

        resolved = resolve_change_ref(change)
        self.assertEqual(resolved["ref"], "refs/changes/47/4247/3")
        self.assertEqual(resolved["source"], "fallback")

    def test_resolve_change_ref_supports_raw_gerrit_revisions_mapping(self) -> None:
        change = {
            "_number": 4247,
            "current_revision": "deadbeef",
            "revisions": {
                "deadbeef": {
                    "_number": 3,
                    "ref": "refs/changes/47/4247/3",
                }
            },
        }

        resolved = resolve_change_ref(change)
        self.assertEqual(resolved["revision"], "deadbeef")
        self.assertEqual(resolved["patch_set"], 3)
        self.assertEqual(resolved["source"], "rest")

    def test_normalize_review_ref_options_expands_repeatable_fields(self) -> None:
        items = normalize_review_ref_option_items(
            {
                "topic": "feature/demo",
                "hashtag": ["release-1", "qa"],
                "reviewer": "alice@example.com",
                "cc": ["bob@example.com", "carol@example.com"],
                "wip": True,
            }
        )

        self.assertEqual(
            items,
            [
                ("topic", "feature/demo"),
                ("hashtag", "release-1"),
                ("hashtag", "qa"),
                ("reviewer", "alice@example.com"),
                ("cc", "bob@example.com"),
                ("cc", "carol@example.com"),
                ("wip", None),
            ],
        )

    def test_build_review_ref_encodes_supported_options(self) -> None:
        ref = build_review_ref(
            "master",
            {
                "topic": "feature/demo",
                "hashtag": ["release-1", "qa"],
                "reviewer": "alice@example.com",
                "cc": "bob@example.com",
                "wip": True,
            },
        )

        self.assertEqual(
            ref,
            "refs/for/master%topic=feature%2Fdemo,hashtag=release-1,hashtag=qa,"
            "reviewer=alice@example.com,cc=bob@example.com,wip",
        )

    def test_build_review_ref_rejects_wip_and_ready_together(self) -> None:
        with self.assertRaisesRegex(ValueError, "wip and ready"):
            build_review_ref("master", {"wip": True, "ready": True})

    def test_remote_url_matches_base_across_git_protocols(self) -> None:
        self.assertTrue(
            remote_url_matches_base(
                "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
                "https://gerrit.example.com/platform/tools",
            )
        )

    def test_remote_matches_project_accepts_project_suffix(self) -> None:
        remote = {
            "name": "gerrit",
            "fetch_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
            "push_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
        }
        self.assertTrue(remote_matches_project(remote, "platform/tools/active-gerrit"))

    def test_select_gerrit_remote_prefers_explicit_remote(self) -> None:
        remotes = [
            {"name": "origin", "fetch_url": "https://github.com/acme/demo.git", "push_url": "https://github.com/acme/demo.git"},
            {
                "name": "gerrit",
                "fetch_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
                "push_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
            },
        ]

        selected = select_gerrit_remote(remotes, explicit_remote="gerrit")
        self.assertEqual(selected["name"], "gerrit")
        self.assertEqual(selected["reason"], "explicit_remote")
        self.assertEqual(selected["warnings"], [])

    def test_select_gerrit_remote_uses_env_and_base_url_matching(self) -> None:
        remotes = [
            {"name": "origin", "fetch_url": "https://github.com/acme/demo.git", "push_url": "https://github.com/acme/demo.git"},
            {
                "name": "gerrit",
                "fetch_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
                "push_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
            },
        ]

        selected = select_gerrit_remote(remotes, env={"GERRIT_GIT_REMOTE": "gerrit"})
        self.assertEqual(selected["name"], "gerrit")
        self.assertEqual(selected["reason"], "env_gerrit_git_remote")

        selected = select_gerrit_remote(
            remotes,
            env={"GERRIT_BASE_URL": "https://gerrit.example.com/platform/tools"},
            project="platform/tools/active-gerrit",
        )
        self.assertEqual(selected["name"], "gerrit")
        self.assertEqual(selected["reason"], "base_url_match")

    def test_select_gerrit_remote_falls_back_with_diagnostic_warning(self) -> None:
        remotes = [
            {
                "name": "origin",
                "fetch_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
                "push_url": "ssh://gerrit.example.com:29418/platform/tools/active-gerrit.git",
            },
            {
                "name": "backup",
                "fetch_url": "ssh://gerrit.example.com:29418/platform/mirror/active-gerrit.git",
                "push_url": "ssh://gerrit.example.com:29418/platform/mirror/active-gerrit.git",
            },
        ]

        selected = select_gerrit_remote(remotes, env={"GERRIT_BASE_URL": "https://gerrit.example.com"})
        self.assertEqual(selected["name"], "origin")
        self.assertEqual(selected["reason"], "origin_fallback")
        self.assertIn("Multiple remotes match GERRIT_BASE_URL", selected["warnings"][0])

    def test_select_gerrit_remote_warns_when_project_does_not_match(self) -> None:
        remotes = [
            {
                "name": "origin",
                "fetch_url": "ssh://gerrit.example.com:29418/platform/other-project.git",
                "push_url": "ssh://gerrit.example.com:29418/platform/other-project.git",
            }
        ]

        selected = select_gerrit_remote(remotes, project="platform/tools/active-gerrit")
        self.assertEqual(selected["reason"], "origin_fallback")
        self.assertIn("does not clearly match Gerrit project", selected["warnings"][0])


if __name__ == "__main__":
    unittest.main()
