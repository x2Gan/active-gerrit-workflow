#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "active-gerrit" / "scripts" / "gerrit_cache.py"
SPEC = importlib.util.spec_from_file_location("gerrit_cache", MODULE_PATH)
gerrit_cache = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = gerrit_cache
SPEC.loader.exec_module(gerrit_cache)


class GerritCacheTests(unittest.TestCase):
    def test_build_cache_key_is_stable_for_equivalent_payloads(self):
        scope = {
            "base_url": "https://gerrit.example.com",
            "auth_type": "basic",
            "username": "alice",
        }
        payload_a = {
            "query": "status:open",
            "limit": 25,
            "options": ["CURRENT_REVISION", "DETAILED_ACCOUNTS"],
        }
        payload_b = {
            "options": ["CURRENT_REVISION", "DETAILED_ACCOUNTS"],
            "limit": 25,
            "query": "status:open",
        }

        key_a = gerrit_cache.build_cache_key("changes/query", scope, payload_a)
        key_b = gerrit_cache.build_cache_key("changes/query", scope, payload_b)

        self.assertEqual(key_a, key_b)
        self.assertTrue(key_a.startswith("changes/query/"))

    def test_path_for_uses_hashed_json_filename(self):
        cache = gerrit_cache.GerritCache("/tmp/example-cache")
        key = gerrit_cache.build_cache_key(
            "accounts/self",
            {"base_url": "https://gerrit.example.com", "auth_type": "basic", "username": "alice"},
            {"command": "whoami"},
        )

        path = cache.path_for(key)

        self.assertEqual(path.suffix, ".json")
        self.assertRegex(path.name, re.compile(r"^[0-9a-f]{64}\.json$"))

    def test_cache_entry_expires_after_ttl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = gerrit_cache.GerritCache(temp_dir)
            key = gerrit_cache.build_cache_key(
                "server/version",
                {"base_url": "https://gerrit.example.com", "auth_type": "anonymous", "username": None},
                {"command": "version"},
            )

            cache.set(key, {"version": "3.11.2"}, ttl_seconds=10, now=100.0)

            self.assertEqual(cache.get(key, now=105.0), {"version": "3.11.2"})
            self.assertIsNone(cache.get(key, now=110.0))
            self.assertFalse(cache.path_for(key).exists())


if __name__ == "__main__":
    unittest.main()