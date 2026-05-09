#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gerrit_client = load_module("gerrit_client", SCRIPTS_DIR / "gerrit_client.py")
gerrit_cache = load_module("gerrit_cache", SCRIPTS_DIR / "gerrit_cache.py")
gerrit_errors = load_module("gerrit_errors", SCRIPTS_DIR / "gerrit_errors.py")
gerrit_cli = load_module("gerrit_cli", SCRIPTS_DIR / "gerrit_cli.py")


class GerritErrorsTests(unittest.TestCase):
    def test_http_error_descriptor_maps_expected_statuses(self):
        cases = {
            400: ("GerritBadRequest", "Check request arguments, JSON payload, and Gerrit field names."),
            401: ("GerritAuthError", "Check GERRIT_USERNAME and GERRIT_HTTP_PASSWORD."),
            403: ("GerritPermissionError", "Check Gerrit project permission or capability for this operation."),
            404: ("GerritNotFound", "The resource does not exist or is not visible to the current user."),
            409: ("GerritConflict", "Refresh change state and resolve the Gerrit state conflict before retrying."),
            412: ("GerritPreconditionFailed", "Refresh the resource and verify required preconditions before retrying."),
            500: ("GerritHTTPError", "Check Gerrit response details and request arguments."),
        }

        for status, (error_type, hint) in cases.items():
            with self.subTest(status=status):
                descriptor = gerrit_errors.http_error_descriptor(status)
                self.assertEqual(descriptor.type, error_type)
                self.assertEqual(descriptor.hint, hint)
                self.assertEqual(descriptor.status, status)

    def test_transport_error_descriptor_classifies_common_failures(self):
        cases = {
            "GET https://gerrit.example.com timed out": "GerritTimeoutError",
            "GET https://gerrit.example.com failed: certificate verify failed": "GerritTLSError",
            "GET https://gerrit.example.com failed: [Errno -2] Name or service not known": "GerritDNSError",
            "GET https://gerrit.example.com failed: [Errno 111] Connection refused": "GerritConnectionError",
            "GET https://gerrit.example.com failed: proxy dropped request": "TransportError",
        }

        for message, error_type in cases.items():
            with self.subTest(message=message):
                descriptor = gerrit_errors.transport_error_descriptor(message)
                self.assertEqual(descriptor.type, error_type)
                self.assertIsNotNone(descriptor.hint)

    def test_describe_exception_maps_http_error(self):
        response = gerrit_client.GerritResponse(
            method="GET",
            url="https://gerrit.example.com/a/changes/missing/detail",
            status=404,
            reason="Not Found",
            headers={},
            content_type="text/plain; charset=UTF-8",
            text="not found",
            data="not found",
        )
        error = gerrit_client.GerritHTTPError(response, redactor=lambda value: value)

        descriptor = gerrit_errors.describe_exception(error)

        self.assertEqual(descriptor.type, "GerritNotFound")
        self.assertEqual(descriptor.status, 404)
        self.assertEqual(
            descriptor.hint,
            "The resource does not exist or is not visible to the current user.",
        )


class GerritCliErrorEnvelopeTests(unittest.TestCase):
    def run_with_error(self, argv, exc):
        env = {
            "GERRIT_BASE_URL": "https://gerrit.example.com",
            "GERRIT_AUTH_TYPE": "basic",
            "GERRIT_USERNAME": "alice",
            "GERRIT_HTTP_PASSWORD": "local-secret",
        }

        class FakeClient:
            def version(self):
                raise exc

        stdout = io.StringIO()
        with mock.patch.object(gerrit_cli.GerritClient, "from_env", return_value=FakeClient()):
            with redirect_stdout(stdout):
                exit_code = gerrit_cli.run(argv, env=env)
        return exit_code, json.loads(stdout.getvalue())

    def make_http_error(self, status: int, reason: str) -> Exception:
        response = gerrit_client.GerritResponse(
            method="GET",
            url="https://gerrit.example.com/config/server/version",
            status=status,
            reason=reason,
            headers={},
            content_type="text/plain; charset=UTF-8",
            text=reason.lower(),
            data=reason.lower(),
        )
        return gerrit_client.GerritHTTPError(response, redactor=lambda value: value)

    def test_run_maps_not_found_http_error(self):
        exit_code, payload = self.run_with_error(["version"], self.make_http_error(404, "Not Found"))

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "version")
        self.assertEqual(payload["error"]["type"], "GerritNotFound")
        self.assertEqual(payload["error"]["status"], 404)
        self.assertEqual(
            payload["error"]["hint"],
            "The resource does not exist or is not visible to the current user.",
        )

    def test_run_maps_conflict_http_error(self):
        exit_code, payload = self.run_with_error(["version"], self.make_http_error(409, "Conflict"))

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "GerritConflict")
        self.assertEqual(payload["error"]["status"], 409)
        self.assertEqual(
            payload["error"]["hint"],
            "Refresh change state and resolve the Gerrit state conflict before retrying.",
        )

    def test_run_maps_tls_transport_error(self):
        exit_code, payload = self.run_with_error(
            ["version"],
            gerrit_client.GerritTransportError(
                "GET https://gerrit.example.com/config/server/version failed: certificate verify failed"
            ),
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "GerritTLSError")
        self.assertIn("GERRIT_VERIFY_SSL", payload["error"]["hint"])

    def test_run_maps_timeout_transport_error(self):
        exit_code, payload = self.run_with_error(
            ["version"],
            gerrit_client.GerritTransportError(
                "GET https://gerrit.example.com/config/server/version timed out"
            ),
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "GerritTimeoutError")
        self.assertIn("GERRIT_TIMEOUT_SECONDS", payload["error"]["hint"])


if __name__ == "__main__":
    unittest.main()