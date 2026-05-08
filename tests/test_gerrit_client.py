#!/usr/bin/env python3

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib import parse


MODULE_PATH = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts" / "gerrit_client.py"
SPEC = importlib.util.spec_from_file_location("gerrit_client", MODULE_PATH)
gerrit_client = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = gerrit_client
SPEC.loader.exec_module(gerrit_client)


EXPECTED_AUTH = "Basic " + base64.b64encode(b"alice:s3cr3t").decode("ascii")


class FakeGerritHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):
        self._record()
        parsed = parse.urlsplit(self.path)
        if parsed.path == "/config/server/version":
            self._send(200, b")]}'\n\"3.11.2\"", "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/accounts/self/detail":
            if self.headers.get("Authorization") != EXPECTED_AUTH:
                self._send(401, b"bad credentials", "text/plain")
                return
            body = b")]}'\n{\"_account_id\":1000001,\"username\":\"alice\"}"
            self._send(200, body, "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/changes/":
            query = parse.parse_qs(parsed.query)
            body = json.dumps(
                {
                    "query": query,
                    "accept": self.headers.get("Accept"),
                    "authorized": self.headers.get("Authorization") == EXPECTED_AUTH,
                }
            ).encode("utf-8")
            self._send(200, b")]}'\n" + body, "application/json; charset=UTF-8")
            return
        if parsed.path == "/a/plain":
            self._send(200, b"hello", "text/plain; charset=UTF-8")
            return
        if parsed.path == "/a/boom":
            body = b"Authorization: Basic abc\npassword=s3cr3t access_token=foo"
            self._send(409, body, "text/plain; charset=UTF-8")
            return
        self._send(404, b"not found", "text/plain; charset=UTF-8")

    def do_POST(self):
        self._record()
        parsed = parse.urlsplit(self.path)
        if parsed.path == "/a/echo":
            body = self._read_body()
            response = {
                "method": "POST",
                "body": json.loads(body.decode("utf-8")),
                "content_type": self.headers.get("Content-Type"),
                "accept": self.headers.get("Accept"),
            }
            self._send(200, b")]}'\n" + json.dumps(response).encode("utf-8"), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def do_PUT(self):
        self._record()
        parsed = parse.urlsplit(self.path)
        if parsed.path == "/a/plain":
            self._send(200, self._read_body(), "text/plain; charset=UTF-8")
            return
        self._send(404, b"not found", "text/plain")

    def do_DELETE(self):
        self._record()
        parsed = parse.urlsplit(self.path)
        if parsed.path == "/a/delete":
            self._send(204, b"", "text/plain")
            return
        self._send(404, b"not found", "text/plain")

    def _record(self):
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
            }
        )

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        if body:
            self.wfile.write(body)


class GerritClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeGerritHandler)
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def make_client(self):
        config = gerrit_client.GerritConfig(
            base_url=self.base_url + "///",
            username="alice",
            http_password="s3cr3t",
            timeout_seconds=5,
        )
        self.assertEqual(config.base_url, self.base_url)
        return gerrit_client.GerritClient(config)

    def test_version_and_whoami_parse_xssi_and_auth_path(self):
        client = self.make_client()

        version = client.version()
        whoami = client.whoami()

        self.assertEqual(version.data, "3.11.2")
        self.assertEqual(whoami.data["_account_id"], 1000001)
        self.assertEqual(whoami.data["username"], "alice")
        self.assertTrue(any(req["path"] == "/config/server/version" for req in self.server.requests))
        self.assertTrue(any(req["path"] == "/a/accounts/self/detail" for req in self.server.requests))

    def test_repeated_query_params_and_json_post(self):
        client = self.make_client()

        response = client.get(
            "/changes/",
            query=[
                ("q", "reviewer:self -owner:self status:open"),
                ("o", "CURRENT_REVISION"),
                ("o", "DETAILED_LABELS"),
            ],
        )
        posted = client.post("/echo", json_body={"message": "hello"})

        self.assertEqual(response.data["query"]["o"], ["CURRENT_REVISION", "DETAILED_LABELS"])
        self.assertEqual(response.data["query"]["q"], ["reviewer:self -owner:self status:open"])
        self.assertEqual(response.data["accept"], "application/json")
        self.assertTrue(response.data["authorized"])
        self.assertEqual(posted.data["body"], {"message": "hello"})
        self.assertEqual(posted.data["content_type"], "application/json; charset=UTF-8")

    def test_put_delete_and_plain_text_dispatch(self):
        client = self.make_client()

        plain = client.get("/plain")
        put = client.put("/plain", data="updated", headers={"Content-Type": "text/plain; charset=UTF-8"})
        deleted = client.delete("/delete")

        self.assertEqual(plain.data, "hello")
        self.assertEqual(put.data, "updated")
        self.assertEqual(deleted.status, 204)
        self.assertIsNone(deleted.data)

    def test_error_strings_redact_password_auth_and_sensitive_query(self):
        client = self.make_client()

        with self.assertRaises(gerrit_client.GerritHTTPError) as raised:
            client.get("/boom", query={"access_token": "secret-token"})

        message = str(raised.exception)
        self.assertIn("HTTP 409", message)
        self.assertIn("access_token=%3Credacted%3E", message)
        self.assertNotIn("s3cr3t", message)
        self.assertNotIn("secret-token", message)
        self.assertNotIn("Basic abc", message)
        self.assertNotIn(EXPECTED_AUTH, message)

    def test_env_config_and_query_encoding_helpers(self):
        config = gerrit_client.GerritConfig.from_env(
            {
                "GERRIT_BASE_URL": self.base_url,
                "GERRIT_AUTH_TYPE": "basic",
                "GERRIT_USERNAME": "alice",
                "GERRIT_HTTP_PASSWORD": "s3cr3t",
                "GERRIT_VERIFY_SSL": "false",
                "GERRIT_TIMEOUT_SECONDS": "7",
            }
        )

        self.assertFalse(config.verify_ssl)
        self.assertEqual(config.timeout_seconds, 7)
        self.assertEqual(
            gerrit_client.encode_query({"o": ["CURRENT_REVISION", "DETAILED_LABELS"], "n": 25}),
            "o=CURRENT_REVISION&o=DETAILED_LABELS&n=25",
        )
        self.assertEqual(gerrit_client.quote_path_segment("platform/foo"), "platform%2Ffoo")


if __name__ == "__main__":
    unittest.main()
