#!/usr/bin/env python3
"""Gerrit-specific Git ref helpers for active-gerrit local Git commands."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlsplit
from urllib.parse import quote
import re

REVIEW_REF_PREFIX = "refs/for"
CHANGE_REF_PREFIX = "refs/changes"
OPTION_ORDER = ("topic", "hashtag", "reviewer", "cc", "wip", "ready")
SCP_LIKE_REMOTE_RE = re.compile(r"^(?:(?P<user>[^@]+)@)?(?P<host>[^:]+):(?P<path>.+)$")


def _strip_git_suffix(path: str) -> str:
    text = path.strip().strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text.strip("/")


def _looks_like_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _normalize_text(value: object, *, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty.")
    return text


def _flatten_option_values(value: object, *, field_name: str) -> list[str]:
    if value is None or value is False:
        return []
    if _looks_like_sequence(value):
        items = []
        for item in value:
            items.append(_normalize_text(item, field_name=field_name))
        return items
    return [_normalize_text(value, field_name=field_name)]


def build_change_ref(change_number: int, patch_set: int) -> str:
    if change_number <= 0:
        raise ValueError("change_number must be greater than zero.")
    if patch_set <= 0:
        raise ValueError("patch_set must be greater than zero.")
    return f"{CHANGE_REF_PREFIX}/{change_number % 100:02d}/{change_number}/{patch_set}"


def build_review_ref(
    branch: str,
    options: Optional[Mapping[str, object] | Sequence[tuple[str, object]]] = None,
) -> str:
    normalized_branch = branch.strip().lstrip("/")
    if not normalized_branch:
        raise ValueError("branch is required to build a Gerrit review ref.")

    ref = f"{REVIEW_REF_PREFIX}/{normalized_branch}"
    if not options:
        return ref

    normalized_items = normalize_review_ref_option_items(options)
    if not normalized_items:
        return ref

    encoded_options = []
    for key, value in normalized_items:
        clean_key = key.strip()
        if value is None:
            encoded_options.append(quote(clean_key, safe=""))
            continue
        encoded_options.append(f"{quote(clean_key, safe='')}={quote(str(value), safe='@._+-')}")
    return f"{ref}%{','.join(encoded_options)}"


def normalize_review_ref_option_items(
    options: Mapping[str, object] | Sequence[tuple[str, object]]
) -> Sequence[tuple[str, Optional[str]]]:
    if isinstance(options, Mapping):
        options_map: Dict[str, object] = dict(options)
        if options_map.get("wip") and options_map.get("ready"):
            raise ValueError("wip and ready cannot both be enabled in the same Gerrit review ref.")

        items: list[tuple[str, Optional[str]]] = []
        topic = options_map.get("topic")
        if topic not in (None, False):
            items.append(("topic", _normalize_text(topic, field_name="topic")))

        for hashtag in _flatten_option_values(options_map.get("hashtag"), field_name="hashtag"):
            items.append(("hashtag", hashtag))
        for reviewer in _flatten_option_values(options_map.get("reviewer"), field_name="reviewer"):
            items.append(("reviewer", reviewer))
        for cc in _flatten_option_values(options_map.get("cc"), field_name="cc"):
            items.append(("cc", cc))

        if options_map.get("wip"):
            items.append(("wip", None))
        if options_map.get("ready"):
            items.append(("ready", None))
        return items

    encoded_options = []
    has_wip = False
    has_ready = False
    for key, value in options:
        clean_key = _normalize_text(key, field_name="review ref option key")
        if clean_key == "wip":
            has_wip = True
        if clean_key == "ready":
            has_ready = True
        if value is None:
            encoded_options.append((clean_key, None))
        else:
            encoded_options.append((clean_key, _normalize_text(value, field_name=clean_key)))
    if has_wip and has_ready:
        raise ValueError("wip and ready cannot both be enabled in the same Gerrit review ref.")
    return encoded_options


def normalize_gerrit_base_url(base_url: str) -> Dict[str, Optional[str]]:
    parsed = urlsplit(base_url.strip())
    host = (parsed.hostname or "").lower() or None
    path = _strip_git_suffix(parsed.path or "")
    return {
        "scheme": parsed.scheme or None,
        "host": host,
        "port": parsed.port,
        "path": path or None,
        "raw": base_url,
    }


def normalize_remote_url(url: str) -> Dict[str, Optional[str]]:
    text = url.strip()
    if not text:
        raise ValueError("remote URL must not be empty.")

    if "://" in text:
        parsed = urlsplit(text)
        return {
            "scheme": parsed.scheme or None,
            "host": (parsed.hostname or "").lower() or None,
            "port": parsed.port,
            "path": _strip_git_suffix(parsed.path or "") or None,
            "scp_like": False,
            "local": False,
            "raw": text,
        }

    scp_match = SCP_LIKE_REMOTE_RE.match(text)
    if scp_match and "/" in scp_match.group("path"):
        return {
            "scheme": "ssh",
            "host": scp_match.group("host").lower(),
            "port": None,
            "path": _strip_git_suffix(scp_match.group("path")) or None,
            "scp_like": True,
            "local": False,
            "raw": text,
        }

    return {
        "scheme": "file",
        "host": None,
        "port": None,
        "path": _strip_git_suffix(text) or None,
        "scp_like": False,
        "local": True,
        "raw": text,
    }


def remote_url_matches_base(url: str, base_url: str) -> bool:
    remote = normalize_remote_url(url)
    base = normalize_gerrit_base_url(base_url)
    if not remote.get("host") or not base.get("host"):
        return False
    if remote["host"] != base["host"]:
        return False
    base_path = base.get("path")
    if not base_path:
        return True
    remote_path = remote.get("path") or ""
    return remote_path == base_path or remote_path.startswith(base_path + "/") or base_path.startswith(remote_path + "/")


def remote_matches_project(remote: Mapping[str, Any] | str, project: str) -> bool:
    project_path = _strip_git_suffix(project)
    if not project_path:
        raise ValueError("project must not be empty.")

    urls: list[str]
    if isinstance(remote, str):
        urls = [remote]
    else:
        urls = []
        for key in ("push_url", "fetch_url"):
            value = remote.get(key)
            if isinstance(value, str) and value.strip():
                urls.append(value)
        for key in ("push_urls", "fetch_urls"):
            values = remote.get(key)
            if _looks_like_sequence(values):
                for value in values:
                    if isinstance(value, str) and value.strip():
                        urls.append(value)

    for url in urls:
        normalized = normalize_remote_url(url)
        remote_path = normalized.get("path") or ""
        if remote_path == project_path or remote_path.endswith("/" + project_path):
            return True
    return False


def select_gerrit_remote(
    remotes: Sequence[Mapping[str, Any]],
    *,
    explicit_remote: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    warnings: list[str] = []
    if not remotes:
        raise ValueError("No Git remotes are configured.")

    env = env or {}

    def find_remote(name: str) -> Optional[Mapping[str, Any]]:
        for remote in remotes:
            if remote.get("name") == name:
                return remote
        return None

    def choose(remote: Mapping[str, Any], reason: str) -> Dict[str, Any]:
        if project and not remote_matches_project(remote, project):
            warnings.append(
                f"Selected remote {remote.get('name')} does not clearly match Gerrit project {project}."
            )
        return {
            "remote": remote,
            "name": remote.get("name"),
            "reason": reason,
            "warnings": warnings,
        }

    if explicit_remote:
        remote = find_remote(explicit_remote)
        if remote is None:
            raise ValueError(f"Requested remote {explicit_remote} is not configured.")
        return choose(remote, "explicit_remote")

    env_remote = (env.get("GERRIT_GIT_REMOTE") or "").strip()
    if env_remote:
        remote = find_remote(env_remote)
        if remote is None:
            warnings.append(f"GERRIT_GIT_REMOTE={env_remote} is not configured in this repository.")
        else:
            return choose(remote, "env_gerrit_git_remote")

    base_url = (env.get("GERRIT_BASE_URL") or "").strip()
    if base_url:
        base_matches = []
        for remote in remotes:
            urls = []
            for key in ("push_url", "fetch_url"):
                value = remote.get(key)
                if isinstance(value, str) and value.strip():
                    urls.append(value)
            if any(remote_url_matches_base(url, base_url) for url in urls):
                base_matches.append(remote)
        if len(base_matches) == 1:
            return choose(base_matches[0], "base_url_match")
        if len(base_matches) > 1:
            if project:
                project_matches = [remote for remote in base_matches if remote_matches_project(remote, project)]
                if len(project_matches) == 1:
                    return choose(project_matches[0], "base_url_and_project_match")
            warnings.append("Multiple remotes match GERRIT_BASE_URL; falling back to later selection rules.")

    origin = find_remote("origin")
    if origin is not None:
        return choose(origin, "origin_fallback")

    if len(remotes) == 1:
        warnings.append("No explicit Gerrit remote was configured; using the only available remote.")
        return choose(remotes[0], "sole_remote_fallback")

    raise ValueError("Could not determine a Gerrit remote; set --remote or GERRIT_GIT_REMOTE.")


def _change_summary(change_document: Mapping[str, Any]) -> Mapping[str, Any]:
    summary = change_document.get("summary")
    if isinstance(summary, Mapping):
        return summary
    return change_document


def _normalize_revisions(change_document: Mapping[str, Any]) -> Sequence[Dict[str, Any]]:
    revisions = change_document.get("revisions")
    if isinstance(revisions, Sequence) and not isinstance(revisions, (str, bytes, bytearray)):
        items = []
        for revision in revisions:
            if isinstance(revision, Mapping):
                items.append(
                    {
                        "revision": revision.get("revision"),
                        "patch_set": revision.get("patch_set"),
                        "ref": revision.get("ref"),
                    }
                )
        return items
    if isinstance(revisions, Mapping):
        items = []
        for revision_sha, revision in revisions.items():
            if not isinstance(revision, Mapping):
                continue
            items.append(
                {
                    "revision": revision.get("revision") or revision_sha,
                    "patch_set": revision.get("patch_set") or revision.get("_number"),
                    "ref": revision.get("ref"),
                }
            )
        return items
    return []


def resolve_change_ref(change_document: Mapping[str, Any], revision: str = "current") -> Dict[str, Any]:
    revisions = _normalize_revisions(change_document)
    if not revisions:
        raise ValueError("Change document did not include any revisions.")

    summary = _change_summary(change_document)
    requested = (revision or "current").strip()

    selected: Optional[Mapping[str, Any]] = None
    if requested == "current":
        current_patch_set = summary.get("current_patch_set")
        current_revision = summary.get("current_revision")
        for candidate in revisions:
            if current_patch_set is not None and str(candidate.get("patch_set")) == str(current_patch_set):
                selected = candidate
                break
        if selected is None and current_revision:
            for candidate in revisions:
                if str(candidate.get("revision")) == str(current_revision):
                    selected = candidate
                    break
        if selected is None and len(revisions) == 1:
            selected = revisions[0]
    else:
        for candidate in revisions:
            if str(candidate.get("patch_set")) == requested or str(candidate.get("revision")) == requested or str(candidate.get("ref")) == requested:
                selected = candidate
                break

    if selected is None:
        raise ValueError(f"Revision {requested} could not be resolved from the change document.")

    ref = selected.get("ref")
    source = "rest"
    if not ref:
        change_number = summary.get("number") or summary.get("_number")
        patch_set = selected.get("patch_set")
        if change_number is None or patch_set is None:
            raise ValueError("Selected revision did not include ref and fallback change/patch set data is incomplete.")
        ref = build_change_ref(int(change_number), int(patch_set))
        source = "fallback"

    return {
        "ref": ref,
        "revision": selected.get("revision"),
        "patch_set": selected.get("patch_set"),
        "requested_revision": requested,
        "source": source,
    }
