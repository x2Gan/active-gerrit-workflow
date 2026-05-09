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


def account_alice():
    return {
        "_account_id": 1000001,
        "name": "Alice",
        "email": "alice@example.com",
        "username": "alice",
    }


def account_bob():
    return {
        "_account_id": 1000002,
        "name": "Bob",
        "email": "bob@example.com",
        "username": "bob",
    }


def account_carol():
    return {
        "_account_id": 1000003,
        "name": "Carol",
        "email": "carol@example.com",
        "username": "carol",
    }


def attention_entry(account, reason):
    return {
        "account": dict(account),
        "last_update": "2026-05-08 09:50:00.000000000",
        "reason": reason,
    }


def initial_change_state():
    return {
        "work_in_progress": False,
        "topic": "feature-x",
        "hashtags": ["feature-x"],
        "attention_set": {
            "1000002": attention_entry(account_bob(), "Reviewer was added"),
        },
        "change_status": "NEW",
        "submittable": True,
        "submit_requirements": [
            {
                "name": "Code-Review",
                "status": "SATISFIED",
                "submittability_expression_result": {
                    "expression": "label:Code-Review=MAX,user=non_uploader",
                    "passing_atoms": ["label:Code-Review=MAX,user=non_uploader"],
                    "failing_atoms": [],
                },
            },
            {
                "name": "Verified",
                "status": "SATISFIED",
                "submittability_expression_result": {
                    "expression": "label:Verified=MAX",
                    "passing_atoms": ["label:Verified=MAX"],
                    "failing_atoms": [],
                },
            },
        ],
        "submit_action": {"method": "POST", "label": "Submit"},
        "mergeable": {
            "mergeable": True,
            "submit_type": "MERGE_IF_NECESSARY",
            "strategy": "recursive",
        },
        "submitted_together_non_visible_changes": 0,
        "current_revision_sha": "abc123",
        "patch_set": 3,
    }


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
            "/a/changes/myProject~4247/revisions/3/mergeable",
            "/a/changes/myProject~4247/revisions/current/mergeable",
        ):
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self.server.state["mergeable"]).encode("utf-8")  # type: ignore[attr-defined]
            self._send(200, b")]}\'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/submitted_together":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self._submitted_together()).encode("utf-8")
            self._send(200, b")]}\'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/attention":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(list(self.server.state["attention_set"].values())).encode("utf-8")  # type: ignore[attr-defined]
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path.startswith("/a/changes/myProject~4247/revisions/") and parsed.path.endswith("/files/"):
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            body = json.dumps(self._files()).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path.startswith("/a/changes/myProject~4247/revisions/") and parsed.path.endswith("/files/src%2Fmain%2FApp.java/diff"):
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

    def do_POST(self):
        body = self._read_body()
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body.decode("utf-8"),
            }
        )
        parsed = parse.urlsplit(self.path)
        if parsed.path == "/a/changes/myProject~4247/reviewers":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8"))
            account = self._account_for_identifier(payload.get("reviewer"))
            response = {
                "input": payload.get("reviewer"),
                "reviewers": [account] if payload.get("state") != "CC" else [],
                "ccs": [account] if payload.get("state") == "CC" else [],
                "confirm": False,
            }
            self._send(200, b")]}'\n" + json.dumps(response).encode("utf-8"), "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/hashtags":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8"))
            hashtags = list(self.server.state["hashtags"])  # type: ignore[attr-defined]
            for tag in payload.get("remove", []):
                hashtags = [item for item in hashtags if item != tag]
            for tag in payload.get("add", []):
                if tag not in hashtags:
                    hashtags.append(tag)
            self.server.state["hashtags"] = hashtags  # type: ignore[attr-defined]
            self._send(200, b")]}'\n" + json.dumps(hashtags).encode("utf-8"), "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/attention":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8"))
            account = self._account_for_identifier(payload.get("user"))
            self.server.state["attention_set"][str(account["_account_id"])] = attention_entry(  # type: ignore[attr-defined]
                account,
                payload.get("reason") or "reason",
            )
            self._send(200, b")]}'\n" + json.dumps(account).encode("utf-8"), "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/attention/1000002/delete":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            self.server.state["attention_set"].pop("1000002", None)  # type: ignore[attr-defined]
            self._send(204, b"", "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/submit":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8")) if body else {}
            self.server.state["change_status"] = "MERGED"  # type: ignore[attr-defined]
            self.server.state["submittable"] = False  # type: ignore[attr-defined]
            self.server.state["submit_action"] = None  # type: ignore[attr-defined]
            response = {
                "status": "MERGED",
                "notify": payload.get("notify"),
                "ref_updates": [
                    {
                        "ref": "refs/heads/master",
                        "new": self.server.state["current_revision_sha"],  # type: ignore[attr-defined]
                    }
                ],
            }
            self._send(200, b")]}'\n" + json.dumps(response).encode("utf-8"), "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/rebase":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8")) if body else {}
            self.server.state["patch_set"] += 1  # type: ignore[attr-defined]
            new_patch_set = self.server.state["patch_set"]  # type: ignore[attr-defined]
            self.server.state["current_revision_sha"] = f"rebased{new_patch_set}"  # type: ignore[attr-defined]
            response = {
                "base": payload.get("base"),
                "allow_conflicts": payload.get("allow_conflicts", False),
                "ref_updates": [
                    {
                        "ref": f"refs/changes/47/4247/{new_patch_set}",
                        "new": self.server.state["current_revision_sha"],  # type: ignore[attr-defined]
                    }
                ],
            }
            self._send(200, b")]}'\n" + json.dumps(response).encode("utf-8"), "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/abandon":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8")) if body else {}
            self.server.state["change_status"] = "ABANDONED"  # type: ignore[attr-defined]
            self.server.state["submittable"] = False  # type: ignore[attr-defined]
            self.server.state["submit_action"] = None  # type: ignore[attr-defined]
            response = {
                "status": "ABANDONED",
                "message": payload.get("message"),
                "notify": payload.get("notify"),
            }
            self._send(200, b")]}'\n" + json.dumps(response).encode("utf-8"), "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/myProject~4247/restore":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8")) if body else {}
            self.server.state["change_status"] = "NEW"  # type: ignore[attr-defined]
            self.server.state["submittable"] = True  # type: ignore[attr-defined]
            self.server.state["submit_action"] = {"method": "POST", "label": "Submit"}  # type: ignore[attr-defined]
            response = {
                "status": "NEW",
                "message": payload.get("message"),
                "notify": payload.get("notify"),
            }
            self._send(200, b")]}'\n" + json.dumps(response).encode("utf-8"), "application/json; charset=UTF-8")
            return
        if parsed.path in (
            "/a/changes/myProject~4247/revisions/3/review",
            "/a/changes/myProject~4247/revisions/2/review",
        ):
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8"))
            if payload.get("work_in_progress"):
                self.server.state["work_in_progress"] = True  # type: ignore[attr-defined]
                self.server.state["attention_set"] = {}  # type: ignore[attr-defined]
            if payload.get("ready"):
                self.server.state["work_in_progress"] = False  # type: ignore[attr-defined]
                self.server.state["attention_set"] = {  # type: ignore[attr-defined]
                    "1000002": attention_entry(account_bob(), "Ready for review"),
                }
            response = {
                "labels": payload.get("labels", {}),
                "comments": payload.get("comments", {}),
                "message": payload.get("message"),
                "tag": payload.get("tag"),
                "notify": payload.get("notify"),
                "work_in_progress": payload.get("work_in_progress"),
                "ready": payload.get("ready"),
            }
            self._send(200, b")]}'\n" + json.dumps(response).encode("utf-8"), "application/json; charset=UTF-8")
            return
        self._send(404, b"not found", "text/plain; charset=UTF-8")

    def do_PUT(self):
        body = self._read_body()
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body.decode("utf-8"),
            }
        )
        parsed = parse.urlsplit(self.path)
        if parsed.path == "/a/changes/myProject~4247/topic":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            payload = json.loads(body.decode("utf-8")) if body else {}
            topic = payload.get("topic")
            self.server.state["topic"] = topic if topic else None  # type: ignore[attr-defined]
            if topic:
                self._send(200, b")]}'\n" + json.dumps(topic).encode("utf-8"), "application/json; charset=UTF-8")
            else:
                self._send(204, b"", "application/json; charset=UTF-8")
            return
        self._send(404, b"not found", "text/plain; charset=UTF-8")

    def do_DELETE(self):
        body = self._read_body()
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body.decode("utf-8"),
            }
        )
        parsed = parse.urlsplit(self.path)
        if parsed.path in (
            "/a/changes/myProject~4247/reviewers/1000002",
            "/a/changes/myProject~4247/reviewers/1000002/votes/Code-Review",
        ):
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain; charset=UTF-8")
                return
            self._send(204, b"", "application/json; charset=UTF-8")
            return
        self._send(404, b"not found", "text/plain; charset=UTF-8")

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def _change_detail(self, project, options):
        account = account_alice()
        reviewer = account_bob()
        state = self.server.state  # type: ignore[attr-defined]
        current_revision = state["current_revision_sha"]
        patch_set = state["patch_set"]
        data = {
            "id": f"{project}~master~Iabc",
            "_number": 4247,
            "project": project,
            "branch": "master",
            "change_id": "Iabc",
            "subject": "Fix bug",
            "status": state["change_status"],
            "created": "2026-05-07 10:00:00.000000000",
            "updated": "2026-05-08 10:00:00.000000000",
            "owner": account,
            "current_revision": current_revision,
            "revisions": {
                current_revision: {
                    "_number": patch_set,
                    "created": "2026-05-08 09:00:00.000000000",
                    "uploader": account,
                    "ref": f"refs/changes/47/4247/{patch_set}",
                    "fetch": {"http": {"url": "https://gerrit.example.com/myProject", "ref": f"refs/changes/47/4247/{patch_set}"}},
                    "commit": {
                        "commit": current_revision,
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
                    "values": {
                        "-2": "This shall not be merged",
                        "-1": "I would prefer this is not merged",
                        "0": "No score",
                        "1": "Looks good to me",
                        "2": "Looks good to me, approved",
                    },
                    "all": [{"_account_id": 1000002, "value": 2}],
                },
                "Verified": {
                    "values": {
                        "-1": "Fails",
                        "0": "No score",
                        "1": "Verified",
                    }
                },
            },
            "permitted_labels": {"Code-Review": [-2, -1, 0, 1, 2], "Verified": [-1, 0, 1]},
            "submit_requirements": list(state["submit_requirements"]),
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
            "unresolved_comment_count": 2,
            "hashtags": list(state["hashtags"]),
            "topic": state["topic"],
            "work_in_progress": state["work_in_progress"],
            "attention_set": dict(state["attention_set"]),
        }
        revision = data["revisions"][current_revision]
        if "CURRENT_COMMIT" not in options:
            revision.pop("commit", None)
        if "CURRENT_FILES" not in options:
            revision.pop("files", None)
        if "MESSAGES" not in options:
            data.pop("messages", None)
        if "REVIEWER_UPDATES" not in options:
            data.pop("reviewer_updates", None)
        if "CURRENT_ACTIONS" in options:
            submit_action = state.get("submit_action")
            data["actions"] = {"submit": dict(submit_action)} if isinstance(submit_action, dict) else {}
        else:
            data.pop("actions", None)
        if "SUBMITTABLE" in options:
            data["submittable"] = state.get("submittable")
        return data

    def _submitted_together(self):
        state = self.server.state  # type: ignore[attr-defined]
        return {
            "changes": [
                {
                    "id": "myProject~master~Iabc",
                    "_number": 4247,
                    "project": "myProject",
                    "branch": "master",
                    "change_id": "Iabc",
                    "subject": "Fix bug",
                    "status": state["change_status"],
                    "owner": account_alice(),
                    "updated": "2026-05-08 10:00:00.000000000",
                    "current_revision": state["current_revision_sha"],
                    "revisions": {state["current_revision_sha"]: {"_number": state["patch_set"]}},
                },
                {
                    "id": "myProject~master~Idef",
                    "_number": 4248,
                    "project": "myProject",
                    "branch": "master",
                    "change_id": "Idef",
                    "subject": "Update dependency",
                    "status": "NEW",
                    "owner": account_carol(),
                    "updated": "2026-05-08 09:55:00.000000000",
                    "current_revision": "def456",
                    "revisions": {"def456": {"_number": 1}},
                },
            ],
            "non_visible_changes": state["submitted_together_non_visible_changes"],
        }

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

    def _account_for_identifier(self, identifier):
        if str(identifier) in ("1000001", "alice", "alice@example.com"):
            return account_alice()
        if str(identifier) in ("1000002", "bob", "bob@example.com"):
            return account_bob()
        return account_carol()


class GerritCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeDoctorGerritHandler)
        cls.server.requests = []
        cls.server.state = initial_change_state()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def setUp(self):
        self.server.requests.clear()
        self.server.state = initial_change_state()
        self.cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache_dir.cleanup)

    def run_cli(self, *args, env=None):
        actual_env = os.environ.copy()
        actual_env.update(
            {
                "GERRIT_BASE_URL": "https://gerrit.example.com",
                "GERRIT_USERNAME": "alice",
                "GERRIT_HTTP_PASSWORD": "local-secret",
                "GERRIT_ACCESS_TOKEN": "access-secret",
                "GERRIT_CACHE_DIR": self.cache_dir.name,
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
            "GERRIT_CACHE_DIR": self.cache_dir.name,
        }
        env.update(overrides)
        return env

    def latest_query(self):
        request = self.server.requests[-1]
        return parse.parse_qs(parse.urlsplit(request["path"]).query)

    def review_input_file(self, payload):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        with handle:
            json.dump(payload, handle)
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return str(path)

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

    def test_version_uses_cache_after_first_fetch(self):
        self.server.requests.clear()

        first = self.run_cli("version", env=self.gerrit_env())
        second = self.run_cli("version", env=self.gerrit_env())

        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        first_payload = json.loads(first.stdout)
        second_payload = json.loads(second.stdout)
        self.assertEqual(first_payload["meta"]["cache"], "miss")
        self.assertEqual(second_payload["meta"]["cache"], "hit")
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths, ["/config/server/version"])

    def test_no_cache_bypasses_existing_version_cache(self):
        self.run_cli("version", env=self.gerrit_env())
        self.server.requests.clear()

        result = self.run_cli("--no-cache", "version", env=self.gerrit_env())

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["meta"]["cache"], "bypass")
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths, ["/config/server/version"])

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

    def test_refresh_reloads_whoami_cache(self):
        self.server.requests.clear()

        first = self.run_cli("whoami", env=self.gerrit_env())
        second = self.run_cli("--refresh", "whoami", env=self.gerrit_env())

        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        first_payload = json.loads(first.stdout)
        second_payload = json.loads(second.stdout)
        self.assertEqual(first_payload["meta"]["cache"], "miss")
        self.assertEqual(second_payload["meta"]["cache"], "refresh")
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths, ["/a/accounts/self/detail", "/a/accounts/self/detail"])

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

    def test_get_diff_current_uses_resolved_revision_cache_key(self):
        self.server.requests.clear()

        first = self.run_cli(
            "get-diff",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            "--file",
            "src/main/App.java",
            env=self.gerrit_env(),
        )
        second = self.run_cli(
            "get-diff",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            "--file",
            "src/main/App.java",
            env=self.gerrit_env(),
        )

        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        first_payload = json.loads(first.stdout)
        second_payload = json.loads(second.stdout)
        self.assertEqual(first_payload["meta"]["cache"], "miss")
        self.assertEqual(second_payload["meta"]["cache"], "hit")
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        diff_path = "/a/changes/myProject~4247/revisions/3/files/src%2Fmain%2FApp.java/diff"
        self.assertEqual(paths.count("/a/changes/myProject~4247/detail"), 2)
        self.assertEqual(paths.count(diff_path), 1)

    def test_get_diff_current_reloads_after_patch_set_changes(self):
        first = self.run_cli(
            "get-diff",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            "--file",
            "src/main/App.java",
            env=self.gerrit_env(),
        )
        self.assertEqual(first.returncode, 0)

        self.server.state["patch_set"] = 4
        self.server.state["current_revision_sha"] = "def456"
        self.server.requests.clear()

        second = self.run_cli(
            "get-diff",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            "--file",
            "src/main/App.java",
            env=self.gerrit_env(),
        )

        self.assertEqual(second.returncode, 0)
        payload = json.loads(second.stdout)
        self.assertEqual(payload["meta"]["cache"], "miss")
        self.assertEqual(payload["data"]["revision"], "4")
        self.assertEqual(payload["data"]["revision_sha"], "def456")
        self.assertEqual(payload["data"]["patch_set"], 4)
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(
            paths,
            [
                "/a/changes/myProject~4247/detail",
                "/a/changes/myProject~4247/revisions/4/files/src%2Fmain%2FApp.java/diff",
            ],
        )

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

    def test_add_reviewer_posts_reviewer_by_username(self):
        self.server.requests.clear()
        result = self.run_cli(
            "add-reviewer",
            "--change",
            "myProject~4247",
            "--reviewer",
            "bob",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "add-reviewer")
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["operation"], "add-reviewer")
        self.assertEqual(data["state"], "REVIEWER")
        self.assertEqual(data["reviewer_input"], "bob")
        self.assertEqual(data["reviewer"]["username"], "bob")
        self.assertEqual(data["added_reviewers"][0]["account_id"], 1000002)
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths[0], "/a/changes/myProject~4247/detail")
        self.assertEqual(paths[1], "/a/changes/myProject~4247/reviewers")
        post = self.server.requests[-1]
        posted_body = json.loads(post["body"])
        self.assertEqual(posted_body["reviewer"], "bob")
        self.assertEqual(posted_body["state"], "REVIEWER")
        self.assertEqual(posted_body["notify"], "OWNER_REVIEWERS")

    def test_add_reviewer_supports_cc_state_and_confirmed(self):
        self.server.requests.clear()
        result = self.run_cli(
            "add-reviewer",
            "--change",
            "myProject~4247",
            "--reviewer",
            "carol@example.com",
            "--state",
            "CC",
            "--confirmed",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["state"], "CC")
        self.assertTrue(data["confirmed"])
        self.assertEqual(data["reviewer"]["email"], "carol@example.com")
        self.assertEqual(data["added_ccs"][0]["username"], "carol")
        post = self.server.requests[-1]
        posted_body = json.loads(post["body"])
        self.assertEqual(posted_body["reviewer"], "carol@example.com")
        self.assertEqual(posted_body["state"], "CC")
        self.assertTrue(posted_body["confirmed"])

    def test_remove_reviewer_defaults_to_dry_run_and_resolves_email(self):
        self.server.requests.clear()
        result = self.run_cli(
            "remove-reviewer",
            "--change",
            "myProject~4247",
            "--reviewer",
            "bob@example.com",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "remove-reviewer")
        data = payload["data"]
        self.assertTrue(data["dry_run"])
        self.assertTrue(data["requires_confirmation"])
        self.assertEqual(data["reviewer"]["username"], "bob")
        self.assertEqual(data["state"], "REVIEWER")
        self.assertEqual(data["change_summary"]["branch"], "master")
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths, ["/a/changes/myProject~4247/detail"])

    def test_remove_reviewer_deletes_when_yes_is_set(self):
        self.server.requests.clear()
        result = self.run_cli(
            "remove-reviewer",
            "--change",
            "myProject~4247",
            "--reviewer",
            "bob",
            "--yes",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["status"], 204)
        self.assertEqual(data["reviewer"]["account_id"], 1000002)
        delete = self.server.requests[-1]
        self.assertEqual(delete["method"], "DELETE")
        self.assertEqual(parse.urlsplit(delete["path"]).path, "/a/changes/myProject~4247/reviewers/1000002")

    def test_delete_vote_defaults_to_dry_run_and_resolves_account_id(self):
        self.server.requests.clear()
        result = self.run_cli(
            "delete-vote",
            "--change",
            "myProject~4247",
            "--reviewer",
            "1000002",
            "--label",
            "Code-Review",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "delete-vote")
        data = payload["data"]
        self.assertTrue(data["dry_run"])
        self.assertTrue(data["requires_confirmation"])
        self.assertEqual(data["reviewer"]["username"], "bob")
        self.assertEqual(data["label"], "Code-Review")
        self.assertEqual(data["value"], 2)
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths, ["/a/changes/myProject~4247/detail"])

    def test_delete_vote_deletes_when_yes_is_set(self):
        self.server.requests.clear()
        result = self.run_cli(
            "delete-vote",
            "--change",
            "myProject~4247",
            "--reviewer",
            "bob@example.com",
            "--label",
            "Code-Review",
            "--yes",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["status"], 204)
        self.assertEqual(data["label"], "Code-Review")
        delete = self.server.requests[-1]
        self.assertEqual(delete["method"], "DELETE")
        self.assertEqual(
            parse.urlsplit(delete["path"]).path,
            "/a/changes/myProject~4247/reviewers/1000002/votes/Code-Review",
        )

    def test_set_wip_updates_before_after_summary_and_clears_attention(self):
        self.server.requests.clear()
        result = self.run_cli(
            "set-wip",
            "--change",
            "myProject~4247",
            "--reason",
            "Need more local testing.",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "set-wip")
        data = payload["data"]
        self.assertEqual(data["operation"], "set-wip")
        self.assertEqual(data["message"], "Need more local testing.")
        self.assertEqual(data["notify"], "OWNER_REVIEWERS")
        self.assertFalse(data["before"]["work_in_progress"])
        self.assertTrue(data["after"]["work_in_progress"])
        self.assertEqual(data["before"]["attention_count"], 1)
        self.assertEqual(data["after"]["attention_count"], 0)
        post = [request for request in self.server.requests if request.get("method") == "POST"][-1]
        posted_body = json.loads(post["body"])
        self.assertTrue(posted_body["work_in_progress"])
        self.assertEqual(posted_body["message"], "Need more local testing.")
        self.assertEqual(posted_body["notify"], "OWNER_REVIEWERS")

    def test_set_ready_updates_before_after_summary_and_restores_attention(self):
        self.server.state["work_in_progress"] = True
        self.server.state["attention_set"] = {}
        self.server.requests.clear()
        result = self.run_cli(
            "set-ready",
            "--change",
            "myProject~4247",
            "--message",
            "Ready for review now.",
            "--notify",
            "ALL",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertEqual(data["operation"], "set-ready")
        self.assertEqual(data["message"], "Ready for review now.")
        self.assertEqual(data["notify"], "ALL")
        self.assertTrue(data["before"]["work_in_progress"])
        self.assertFalse(data["after"]["work_in_progress"])
        self.assertEqual(data["before"]["attention_count"], 0)
        self.assertEqual(data["after"]["attention_count"], 1)
        post = [request for request in self.server.requests if request.get("method") == "POST"][-1]
        posted_body = json.loads(post["body"])
        self.assertTrue(posted_body["ready"])
        self.assertEqual(posted_body["notify"], "ALL")

    def test_set_topic_updates_topic_and_posts_follow_up_message(self):
        self.server.requests.clear()
        result = self.run_cli(
            "set-topic",
            "--change",
            "myProject~4247",
            "--topic",
            "release-2026-05",
            "--reason",
            "Align with release train.",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "set-topic")
        data = payload["data"]
        self.assertEqual(data["before"]["topic"], "feature-x")
        self.assertEqual(data["after"]["topic"], "release-2026-05")
        self.assertEqual(data["topic"], "release-2026-05")
        self.assertTrue(data["message_posted"])
        put = [request for request in self.server.requests if request.get("method") == "PUT"][-1]
        self.assertEqual(parse.urlsplit(put["path"]).path, "/a/changes/myProject~4247/topic")
        self.assertEqual(json.loads(put["body"]), {"topic": "release-2026-05"})
        post = [request for request in self.server.requests if request.get("method") == "POST"][-1]
        self.assertEqual(parse.urlsplit(post["path"]).path, "/a/changes/myProject~4247/revisions/3/review")
        self.assertEqual(json.loads(post["body"])["message"], "Align with release train.")

    def test_set_hashtags_updates_hashtag_lists(self):
        self.server.requests.clear()
        result = self.run_cli(
            "set-hashtags",
            "--change",
            "myProject~4247",
            "--add",
            "release",
            "--remove",
            "feature-x",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "set-hashtags")
        data = payload["data"]
        self.assertEqual(data["added"], ["release"])
        self.assertEqual(data["removed"], ["feature-x"])
        self.assertEqual(data["before"]["hashtags"], ["feature-x"])
        self.assertEqual(data["after"]["hashtags"], ["release"])
        self.assertFalse(data["message_posted"])
        post = [request for request in self.server.requests if request.get("method") == "POST"][-1]
        self.assertEqual(parse.urlsplit(post["path"]).path, "/a/changes/myProject~4247/hashtags")
        self.assertEqual(json.loads(post["body"]), {"add": ["release"], "remove": ["feature-x"]})

    def test_attention_add_posts_reason_and_updates_attention_set(self):
        self.server.requests.clear()
        result = self.run_cli(
            "attention-add",
            "--change",
            "myProject~4247",
            "--account",
            "alice@example.com",
            "--reason",
            "Owner needs to reply.",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "attention-add")
        data = payload["data"]
        self.assertEqual(data["account"]["username"], "alice")
        self.assertEqual(data["reason"], "Owner needs to reply.")
        self.assertEqual(data["notify"], "OWNER_REVIEWERS")
        self.assertEqual(data["before"]["attention_count"], 1)
        self.assertEqual(data["after"]["attention_count"], 2)
        post = [request for request in self.server.requests if request.get("method") == "POST"][-1]
        self.assertEqual(parse.urlsplit(post["path"]).path, "/a/changes/myProject~4247/attention")
        self.assertEqual(
            json.loads(post["body"]),
            {"user": "alice@example.com", "reason": "Owner needs to reply.", "notify": "OWNER_REVIEWERS"},
        )

    def test_attention_remove_resolves_account_and_uses_post_delete(self):
        self.server.requests.clear()
        result = self.run_cli(
            "attention-remove",
            "--change",
            "myProject~4247",
            "--account",
            "bob@example.com",
            "--message",
            "Bob has responded.",
            "--notify",
            "NONE",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "attention-remove")
        data = payload["data"]
        self.assertEqual(data["account"]["username"], "bob")
        self.assertEqual(data["reason"], "Bob has responded.")
        self.assertEqual(data["notify"], "NONE")
        self.assertEqual(data["before"]["attention_count"], 1)
        self.assertEqual(data["after"]["attention_count"], 0)
        post = [request for request in self.server.requests if request.get("method") == "POST"][-1]
        self.assertEqual(parse.urlsplit(post["path"]).path, "/a/changes/myProject~4247/attention/1000002/delete")
        self.assertEqual(json.loads(post["body"]), {"reason": "Bob has responded.", "notify": "NONE"})

    def test_submit_dry_run_returns_plan_when_change_is_ready(self):
        self.server.requests.clear()
        result = self.run_cli(
            "submit",
            "--change",
            "myProject~4247",
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "submit")
        data = payload["data"]
        self.assertTrue(data["dry_run"])
        self.assertTrue(data["ready"])
        self.assertEqual(data["action"], "submit")
        self.assertEqual(data["current_status"], "NEW")
        self.assertEqual(data["current_revision"], "3")
        self.assertEqual(data["revision_sha"], "abc123")
        self.assertEqual(data["patch_set"], 3)
        self.assertEqual(data["notify"], "ALL")
        self.assertFalse(data["yes"])
        self.assertEqual(data["submit_requirements"]["unsatisfied_count"], 0)
        self.assertEqual(data["mergeable"]["mergeable"], True)
        self.assertEqual(data["submitted_together"]["total_count"], 2)
        self.assertEqual(data["submit_action"]["method"], "POST")
        self.assertEqual(data["planned_request"], {"method": "POST", "path": "/changes/myProject~4247/submit", "body": {"notify": "ALL"}})
        self.assertEqual(data["blockers"], [])
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(
            paths,
            [
                "/a/changes/myProject~4247/detail",
                "/a/changes/myProject~4247/revisions/3/mergeable",
                "/a/changes/myProject~4247/submitted_together",
            ],
        )
        detail_query = parse.parse_qs(parse.urlsplit(self.server.requests[0]["path"]).query)
        self.assertEqual(
            detail_query["o"],
            [
                "CURRENT_REVISION",
                "DETAILED_ACCOUNTS",
                "DETAILED_LABELS",
                "SUBMIT_REQUIREMENTS",
                "CURRENT_ACTIONS",
                "SUBMITTABLE",
            ],
        )

    def test_submit_precheck_bypasses_cached_change_detail(self):
        warm = self.run_cli(
            "get-change",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )
        self.assertEqual(warm.returncode, 0)

        self.server.requests.clear()
        result = self.run_cli(
            "submit",
            "--change",
            "myProject~4247",
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["meta"]["cache"], "not_used")
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(
            paths,
            [
                "/a/changes/myProject~4247/detail",
                "/a/changes/myProject~4247/revisions/3/mergeable",
                "/a/changes/myProject~4247/submitted_together",
            ],
        )
        submitted_together_query = parse.parse_qs(parse.urlsplit(self.server.requests[2]["path"]).query)
        self.assertEqual(submitted_together_query["o"], ["NON_VISIBLE_CHANGES"])

    def test_submit_dry_run_lists_unsatisfied_requirements_and_missing_submit_action(self):
        self.server.requests.clear()
        self.server.state["submit_requirements"] = [
            {
                "name": "Code-Review",
                "status": "UNSATISFIED",
                "fallback_text": "Code-Review +2 is required.",
                "submittability_expression_result": {
                    "expression": "label:Code-Review=MAX,user=non_uploader",
                    "passing_atoms": [],
                    "failing_atoms": ["label:Code-Review=MAX,user=non_uploader"],
                },
            }
        ]
        self.server.state["submittable"] = False
        self.server.state["submit_action"] = None

        result = self.run_cli(
            "submit",
            "--change",
            "myProject~4247",
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertFalse(data["ready"])
        self.assertEqual(data["reason"], "Change is not ready to submit.")
        self.assertEqual(data["submit_requirements"]["unsatisfied_count"], 1)
        self.assertEqual(data["submit_requirements"]["unsatisfied"][0]["name"], "Code-Review")
        blockers = {item["name"]: item for item in data["blockers"]}
        self.assertEqual(set(blockers), {"submit_requirements", "submittable", "submit_action"})
        self.assertIn("Code-Review +2 is required.", blockers["submit_requirements"]["evidence"])
        self.assertEqual(blockers["submittable"]["summary"], "Gerrit reports this change is not submittable.")
        self.assertEqual(blockers["submit_action"]["summary"], "Submit action is not available.")

    def test_submit_dry_run_blocks_non_new_or_unmergeable_change(self):
        self.server.requests.clear()
        self.server.state["change_status"] = "MERGED"
        self.server.state["mergeable"] = {
            "mergeable": False,
            "submit_type": "MERGE_IF_NECESSARY",
            "strategy": "recursive",
        }

        result = self.run_cli(
            "submit",
            "--change",
            "myProject~4247",
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertFalse(data["ready"])
        blockers = {item["name"]: item for item in data["blockers"]}
        self.assertEqual(blockers["change_status"]["summary"], "Change status must be NEW before submit.")
        self.assertEqual(blockers["mergeable"]["summary"], "Current revision is not mergeable.")
        self.assertEqual(data["current_status"], "MERGED")

    def test_submit_without_yes_defaults_to_dry_run_and_does_not_post(self):
        self.server.requests.clear()
        result = self.run_cli(
            "submit",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertTrue(data["dry_run"])
        self.assertFalse(any(request.get("method") == "POST" for request in self.server.requests))

    def test_submit_yes_executes_only_after_checks_pass(self):
        self.server.requests.clear()
        result = self.run_cli(
            "submit",
            "--change",
            "myProject~4247",
            "--yes",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "submit")
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertFalse(data["dry_run"])
        self.assertTrue(data["yes"])
        self.assertEqual(data["operation"], "submit")
        self.assertEqual(data["status"], 200)
        self.assertEqual(data["notify"], "ALL")
        self.assertEqual(data["after"]["status"], "MERGED")
        self.assertEqual(data["updated_refs"], ["refs/heads/master"])
        submit_posts = [request for request in self.server.requests if parse.urlsplit(request["path"]).path == "/a/changes/myProject~4247/submit"]
        self.assertEqual(len(submit_posts), 1)
        self.assertEqual(json.loads(submit_posts[0]["body"]), {"notify": "ALL"})

    def test_submit_yes_does_not_post_when_precheck_is_blocked(self):
        self.server.requests.clear()
        self.server.state["submit_requirements"] = [
            {
                "name": "Code-Review",
                "status": "UNSATISFIED",
                "fallback_text": "Code-Review +2 is required.",
                "submittability_expression_result": {
                    "expression": "label:Code-Review=MAX,user=non_uploader",
                    "passing_atoms": [],
                    "failing_atoms": ["label:Code-Review=MAX,user=non_uploader"],
                },
            }
        ]
        self.server.state["submittable"] = False
        self.server.state["submit_action"] = None

        result = self.run_cli(
            "submit",
            "--change",
            "myProject~4247",
            "--yes",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertFalse(data["ready"])
        self.assertTrue(data["blocked"])
        self.assertFalse(data["executed"])
        submit_posts = [request for request in self.server.requests if parse.urlsplit(request["path"]).path == "/a/changes/myProject~4247/submit"]
        self.assertEqual(submit_posts, [])

    def test_rebase_yes_posts_and_updates_current_revision_ref(self):
        self.server.requests.clear()
        result = self.run_cli(
            "rebase",
            "--change",
            "myProject~4247",
            "--base",
            "myProject~4000",
            "--allow-conflicts",
            "--yes",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "rebase")
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["operation"], "rebase")
        self.assertEqual(data["after"]["current_patch_set"], 4)
        self.assertEqual(data["updated_refs"], ["refs/changes/47/4247/4"])
        post = [request for request in self.server.requests if parse.urlsplit(request["path"]).path == "/a/changes/myProject~4247/rebase"][-1]
        self.assertEqual(json.loads(post["body"]), {"allow_conflicts": True, "base": "myProject~4000"})

    def test_abandon_requires_message(self):
        result = self.run_cli(
            "abandon",
            "--change",
            "myProject~4247",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "ValidationError")

    def test_abandon_yes_posts_message_and_changes_status(self):
        self.server.requests.clear()
        result = self.run_cli(
            "abandon",
            "--change",
            "myProject~4247",
            "--message",
            "Superseded by a newer change.",
            "--yes",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "abandon")
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["notify"], "OWNER")
        self.assertEqual(data["after"]["status"], "ABANDONED")
        post = [request for request in self.server.requests if parse.urlsplit(request["path"]).path == "/a/changes/myProject~4247/abandon"][-1]
        self.assertEqual(
            json.loads(post["body"]),
            {"message": "Superseded by a newer change.", "notify": "OWNER"},
        )

    def test_restore_yes_posts_and_returns_change_to_new(self):
        self.server.requests.clear()
        self.server.state["change_status"] = "ABANDONED"
        self.server.state["submittable"] = False
        self.server.state["submit_action"] = None

        result = self.run_cli(
            "restore",
            "--change",
            "myProject~4247",
            "--reason",
            "Need this change back in review.",
            "--yes",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "restore")
        data = payload["data"]
        self.assertTrue(data["executed"])
        self.assertEqual(data["notify"], "OWNER")
        self.assertEqual(data["after"]["status"], "NEW")
        post = [request for request in self.server.requests if parse.urlsplit(request["path"]).path == "/a/changes/myProject~4247/restore"][-1]
        self.assertEqual(
            json.loads(post["body"]),
            {"message": "Need this change back in review.", "notify": "OWNER"},
        )

    def test_review_dry_run_builds_valid_review_input_plan_with_defaults(self):
        self.server.requests.clear()
        input_path = self.review_input_file(
            {
                "message": "Reviewed by agent.",
                "labels": {"Code-Review": 1},
                "comments": {
                    "src/main/App.java": [
                        {
                            "line": 42,
                            "range": {
                                "start_line": 42,
                                "start_character": 4,
                                "end_line": 42,
                                "end_character": 13,
                            },
                            "message": "Consider extracting this branch.",
                            "unresolved": True,
                        }
                    ],
                    "/PATCHSET_LEVEL": [
                        {
                            "message": "Overall review summary.",
                            "unresolved": False,
                        }
                    ],
                },
            }
        )

        result = self.run_cli(
            "review",
            "--change",
            "myProject~4247",
            "--revision",
            "current",
            "--input",
            input_path,
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "review")
        plan = payload["data"]
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["change"], "myProject~4247")
        self.assertEqual(plan["revision"], "current")
        self.assertEqual(plan["resolved_revision"], "3")
        self.assertEqual(plan["revision_sha"], "abc123")
        self.assertEqual(plan["patch_set"], 3)
        self.assertEqual(plan["message"], "Reviewed by agent.")
        self.assertEqual(plan["labels"], {"Code-Review": 1})
        self.assertEqual(plan["comments_count"], 2)
        self.assertEqual(plan["files"], ["src/main/App.java", "/PATCHSET_LEVEL"])
        self.assertEqual(plan["notify"], "OWNER_REVIEWERS")
        self.assertEqual(plan["tag"], "autogenerated:active-gerrit")
        self.assertEqual(plan["payload"]["notify"], "OWNER_REVIEWERS")
        self.assertEqual(plan["payload"]["tag"], "autogenerated:active-gerrit")
        self.assertEqual(plan["payload"]["comments"]["src/main/App.java"][0]["range"]["end_character"], 13)
        self.assertNotIn("line", plan["payload"]["comments"]["/PATCHSET_LEVEL"][0])
        paths = [parse.urlsplit(request["path"]).path for request in self.server.requests]
        self.assertEqual(paths[0], "/a/changes/myProject~4247/detail")
        self.assertEqual(paths[1], "/a/changes/myProject~4247/revisions/3/files/")

    def test_review_dry_run_accepts_message_label_and_notify_without_input_file(self):
        self.server.requests.clear()
        result = self.run_cli(
            "review",
            "--change",
            "myProject~4247",
            "--message",
            "Looks good.",
            "--label",
            "Code-Review=1",
            "--label",
            "Verified=1",
            "--notify",
            "NONE",
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        plan = payload["data"]
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["message"], "Looks good.")
        self.assertEqual(plan["labels"], {"Code-Review": 1, "Verified": 1})
        self.assertEqual(plan["notify"], "NONE")
        self.assertEqual(plan["comments_count"], 0)

    def test_review_dry_run_rejects_label_value_outside_range(self):
        input_path = self.review_input_file({"labels": {"Code-Review": 3}})

        result = self.run_cli(
            "review",
            "--change",
            "myProject~4247",
            "--input",
            input_path,
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "review")
        self.assertIn("outside allowed values", payload["error"]["message"])

    def test_review_dry_run_rejects_comment_for_missing_file(self):
        input_path = self.review_input_file(
            {
                "comments": {
                    "src/missing/App.java": [
                        {
                            "line": 1,
                            "message": "Missing file.",
                            "unresolved": True,
                        }
                    ]
                }
            }
        )

        result = self.run_cli(
            "review",
            "--change",
            "myProject~4247",
            "--input",
            input_path,
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("not present in the selected revision", payload["error"]["message"])

    def test_review_dry_run_rejects_invalid_comment_range(self):
        input_path = self.review_input_file(
            {
                "comments": {
                    "src/main/App.java": [
                        {
                            "range": {
                                "start_line": 42,
                                "start_character": 8,
                                "end_line": 42,
                                "end_character": 4,
                            },
                            "message": "Bad range.",
                            "unresolved": True,
                        }
                    ]
                }
            }
        )

        result = self.run_cli(
            "review",
            "--change",
            "myProject~4247",
            "--input",
            input_path,
            "--dry-run",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("range end must be after start", payload["error"]["message"])

    def test_review_posts_input_to_resolved_revision(self):
        self.server.requests.clear()
        input_path = self.review_input_file({"message": "Reviewed by agent."})

        result = self.run_cli(
            "review",
            "--change",
            "myProject~4247",
            "--input",
            input_path,
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "review")
        data = payload["data"]
        self.assertTrue(data["posted"])
        self.assertEqual(data["status"], 200)
        self.assertEqual(data["resolved_revision"], "3")
        self.assertEqual(data["message"], "Reviewed by agent.")
        self.assertEqual(data["response"]["message"], "Reviewed by agent.")
        post = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(post["path"]).path, "/a/changes/myProject~4247/revisions/3/review")
        posted_body = json.loads(post["body"])
        self.assertEqual(posted_body["message"], "Reviewed by agent.")
        self.assertEqual(posted_body["tag"], "autogenerated:active-gerrit")
        self.assertEqual(posted_body["notify"], "OWNER_REVIEWERS")

    def test_comment_posts_patchset_level_comment(self):
        self.server.requests.clear()
        result = self.run_cli(
            "comment",
            "--change",
            "myProject~4247",
            "--message",
            "Patch set summary.",
            "--patchset-level",
            "--resolved",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "comment")
        data = payload["data"]
        self.assertTrue(data["posted"])
        self.assertEqual(data["comments_count"], 1)
        post = self.server.requests[-1]
        self.assertEqual(parse.urlsplit(post["path"]).path, "/a/changes/myProject~4247/revisions/3/review")
        posted_body = json.loads(post["body"])
        comment = posted_body["comments"]["/PATCHSET_LEVEL"][0]
        self.assertEqual(comment["message"], "Patch set summary.")
        self.assertFalse(comment["unresolved"])
        self.assertNotIn("line", comment)

    def test_comment_posts_inline_comment(self):
        self.server.requests.clear()
        result = self.run_cli(
            "comment",
            "--change",
            "myProject~4247",
            "--file",
            "src/main/App.java",
            "--line",
            "42",
            "--message",
            "Inline note.",
            "--unresolved",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "comment")
        post = self.server.requests[-1]
        posted_body = json.loads(post["body"])
        comment = posted_body["comments"]["src/main/App.java"][0]
        self.assertEqual(comment["line"], 42)
        self.assertEqual(comment["message"], "Inline note.")
        self.assertTrue(comment["unresolved"])

    def test_vote_posts_label_vote(self):
        self.server.requests.clear()
        result = self.run_cli(
            "vote",
            "--change",
            "myProject~4247",
            "--label",
            "Verified=1",
            "--message",
            "CI passed.",
            env=self.gerrit_env(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "vote")
        data = payload["data"]
        self.assertTrue(data["posted"])
        self.assertEqual(data["labels"], {"Verified": 1})
        self.assertEqual(data["message"], "CI passed.")
        post = self.server.requests[-1]
        posted_body = json.loads(post["body"])
        self.assertEqual(posted_body["labels"], {"Verified": 1})
        self.assertEqual(posted_body["message"], "CI passed.")
        self.assertNotIn("comments", posted_body)


if __name__ == "__main__":
    unittest.main()
