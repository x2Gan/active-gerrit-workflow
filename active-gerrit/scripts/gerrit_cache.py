#!/usr/bin/env python3
"""Small file-based cache for active-gerrit CLI commands."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


CACHE_FORMAT_VERSION = 1
DEFAULT_CACHE_DIR = ".cache/gerrit"


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def stable_json(value: Any) -> str:
    return json.dumps(_normalize(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def build_cache_key(namespace: str, scope: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    descriptor = {
        "namespace": namespace,
        "scope": _normalize(scope),
        "payload": _normalize(payload),
    }
    digest = hashlib.sha256(stable_json(descriptor).encode("utf-8")).hexdigest()
    return f"{namespace}/{digest}"


@dataclass(frozen=True)
class CacheEntry:
    key: str
    value: Any
    created_at: float
    expires_at: float

    def is_expired(self, now: Optional[float] = None) -> bool:
        current_time = time.time() if now is None else float(now)
        return current_time >= self.expires_at


class GerritCache:
    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GerritCache":
        return cls(env.get("GERRIT_CACHE_DIR") or DEFAULT_CACHE_DIR)

    def get(self, key: str, now: Optional[float] = None) -> Optional[Any]:
        path = self.path_for(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            self._safe_unlink(path)
            return None

        if not isinstance(payload, Mapping):
            self._safe_unlink(path)
            return None

        if payload.get("version") != CACHE_FORMAT_VERSION or payload.get("key") != key:
            self._safe_unlink(path)
            return None

        try:
            entry = CacheEntry(
                key=str(payload["key"]),
                value=payload.get("value"),
                created_at=float(payload["created_at"]),
                expires_at=float(payload["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            self._safe_unlink(path)
            return None

        if entry.is_expired(now=now):
            self._safe_unlink(path)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float, now: Optional[float] = None) -> None:
        ttl_value = float(ttl_seconds)
        if ttl_value <= 0:
            raise ValueError("ttl_seconds must be greater than zero.")

        current_time = time.time() if now is None else float(now)
        payload = {
            "version": CACHE_FORMAT_VERSION,
            "key": key,
            "created_at": current_time,
            "expires_at": current_time + ttl_value,
            "value": value,
        }

        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)

    def delete(self, key: str) -> None:
        self._safe_unlink(self.path_for(key))

    def path_for(self, key: str) -> Path:
        namespace, _, digest = key.partition("/")
        if not digest:
            namespace = "default"
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / namespace / f"{digest}.json"

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return