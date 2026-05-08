#!/usr/bin/env python3
"""Low-level Gerrit REST client for active-gerrit scripts."""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union
from urllib import error as urlerror
from urllib import parse, request

XSSI_PREFIX = ")]}'"
DEFAULT_TIMEOUT_SECONDS = 30.0
JSON_CONTENT_TYPE = "application/json; charset=UTF-8"
SUPPORTED_AUTH_TYPES = {"basic", "bearer", "access_token", "cookie_xsrf", "anonymous"}

ScalarQueryValue = Union[str, int, float, bool, None]
QueryValue = Union[ScalarQueryValue, Sequence[ScalarQueryValue]]
QueryParams = Union[Mapping[str, QueryValue], Sequence[Tuple[str, ScalarQueryValue]]]

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-gerrit-auth",
    "x-xsrf-token",
}

SENSITIVE_QUERY_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "password",
    "token",
    "xsrf",
}

URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")


class GerritClientError(Exception):
    """Base error for Gerrit client failures."""


class GerritConfigError(GerritClientError):
    """Invalid local configuration."""


class GerritParseError(GerritClientError):
    """A Gerrit response could not be parsed as expected."""


class GerritTransportError(GerritClientError):
    """Network, TLS, DNS, proxy, or timeout failure."""


@dataclass(frozen=True)
class AuthProvider:
    """Base request authentication hook."""

    auth_type: str

    def apply(
        self,
        path: str,
        query: Optional[QueryParams],
        headers: Dict[str, str],
        authenticated: bool,
    ) -> Tuple[str, Optional[QueryParams]]:
        return path, query

    def redaction_secrets(self) -> Tuple[str, ...]:
        return ()


@dataclass(frozen=True)
class BasicAuthProvider(AuthProvider):
    username: Optional[str] = None
    http_password: Optional[str] = field(default=None, repr=False)

    def __init__(self, username: Optional[str], http_password: Optional[str]) -> None:
        object.__setattr__(self, "auth_type", "basic")
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "http_password", http_password)

    def apply(
        self,
        path: str,
        query: Optional[QueryParams],
        headers: Dict[str, str],
        authenticated: bool,
    ) -> Tuple[str, Optional[QueryParams]]:
        if not authenticated:
            return path, query
        if not path.startswith("/a/"):
            path = "/a" + path
        headers["Authorization"] = make_basic_auth_header(
            self.username or "",
            self.http_password or "",
        )
        return path, query

    def redaction_secrets(self) -> Tuple[str, ...]:
        secrets = []
        if self.http_password:
            secrets.append(self.http_password)
        if self.username and self.http_password:
            secrets.append(make_basic_auth_header(self.username, self.http_password))
        return tuple(secrets)


@dataclass(frozen=True)
class ReservedAuthProvider(AuthProvider):
    message: str
    secrets: Tuple[str, ...] = field(default_factory=tuple, repr=False)

    def apply(
        self,
        path: str,
        query: Optional[QueryParams],
        headers: Dict[str, str],
        authenticated: bool,
    ) -> Tuple[str, Optional[QueryParams]]:
        if authenticated:
            raise GerritConfigError(self.message)
        return path, query

    def redaction_secrets(self) -> Tuple[str, ...]:
        return tuple(secret for secret in self.secrets if secret)


class BearerTokenProvider(ReservedAuthProvider):
    def __init__(self, token: Optional[str]) -> None:
        super().__init__(
            auth_type="bearer",
            message=(
                "GERRIT_AUTH_TYPE=bearer is reserved but not implemented in M1. "
                "Use GERRIT_AUTH_TYPE=basic for authenticated Gerrit requests."
            ),
            secrets=(token,) if token else (),
        )


class AccessTokenProvider(ReservedAuthProvider):
    def __init__(self, token: Optional[str]) -> None:
        super().__init__(
            auth_type="access_token",
            message=(
                "GERRIT_AUTH_TYPE=access_token is reserved but not implemented in M1. "
                "Use GERRIT_AUTH_TYPE=basic for authenticated Gerrit requests."
            ),
            secrets=(token,) if token else (),
        )


class CookieXsrfProvider(ReservedAuthProvider):
    def __init__(self, cookie: Optional[str], xsrf_token: Optional[str]) -> None:
        super().__init__(
            auth_type="cookie_xsrf",
            message=(
                "GERRIT_AUTH_TYPE=cookie_xsrf is reserved but not implemented in M1. "
                "Use GERRIT_AUTH_TYPE=basic for authenticated Gerrit requests."
            ),
            secrets=tuple(secret for secret in (cookie, xsrf_token) if secret),
        )


@dataclass(frozen=True)
class AnonymousProvider(AuthProvider):
    def __init__(self) -> None:
        object.__setattr__(self, "auth_type", "anonymous")


@dataclass(frozen=True)
class GerritResponse:
    method: str
    url: str
    status: int
    reason: str
    headers: Mapping[str, str]
    content_type: str
    text: str
    data: Any


class GerritHTTPError(GerritClientError):
    """HTTP error response from Gerrit."""

    def __init__(
        self,
        response: GerritResponse,
        redactor: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.response = response
        self._redactor = redactor or (lambda value: value)
        super().__init__(self.__str__())

    def __str__(self) -> str:
        body = self.response.text.strip()
        if len(body) > 500:
            body = body[:500] + "...<truncated>"
        body = self._redactor(body)
        base = (
            f"{self.response.method} {self.response.url} failed with "
            f"HTTP {self.response.status} {self.response.reason}"
        )
        return f"{base}: {body}" if body else base


def normalize_base_url(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if not value:
        raise GerritConfigError("GERRIT_BASE_URL is required.")

    parts = parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise GerritConfigError("GERRIT_BASE_URL must be an http(s) URL.")
    if parts.query or parts.fragment:
        raise GerritConfigError("GERRIT_BASE_URL must not include query or fragment.")

    path = parts.path.rstrip("/")
    return parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def parse_bool(value: Optional[str], default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "y", "on"):
        return True
    if normalized in ("0", "false", "no", "n", "off"):
        return False
    raise GerritConfigError(f"Invalid boolean value: {value!r}")


def quote_path_segment(value: object, safe: str = "") -> str:
    return parse.quote(str(value), safe=safe)


def normalize_request_path(path: str) -> str:
    if not path:
        return "/"
    if path.startswith("/"):
        return path
    return "/" + path


def make_basic_auth_header(username: str, password: str) -> str:
    if not username:
        raise GerritConfigError("GERRIT_USERNAME is required for Basic Auth.")
    if not password:
        raise GerritConfigError("GERRIT_HTTP_PASSWORD is required for Basic Auth.")
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def strip_xssi_prefix(text: str) -> str:
    if not text.startswith(XSSI_PREFIX):
        return text
    _, separator, rest = text.partition("\n")
    return rest if separator else ""


def sanitize_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    sanitized: Dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADER_KEYS:
            sanitized[key] = "<redacted>"
        else:
            sanitized[key] = value
    return sanitized


def redact_url(url: str) -> str:
    parts = parse.urlsplit(url)
    query_pairs = parse.parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = []
    for key, value in query_pairs:
        if _is_sensitive_name(key):
            redacted_pairs.append((key, "<redacted>"))
        else:
            redacted_pairs.append((key, value))
    query = parse.urlencode(redacted_pairs)
    return parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def redact_text(text: str, secrets: Iterable[Optional[str]] = ()) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")

    redacted = URL_PATTERN.sub(lambda match: redact_url(match.group(0)), redacted)

    redacted = re.sub(
        r"(?i)(authorization\s*:\s*)(basic|bearer)\s+[-._~+/=a-z0-9]+",
        r"\1<redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(access_token|authorization|cookie|password|token|xsrf)"
        r"(\s*[=:]\s*)([^,\s;]+)",
        r"\1\2<redacted>",
        redacted,
    )
    return redacted


@dataclass(frozen=True)
class GerritConfig:
    base_url: str
    username: Optional[str] = None
    http_password: Optional[str] = field(default=None, repr=False)
    bearer_token: Optional[str] = field(default=None, repr=False)
    access_token: Optional[str] = field(default=None, repr=False)
    cookie: Optional[str] = field(default=None, repr=False)
    xsrf_token: Optional[str] = field(default=None, repr=False)
    auth_type: str = "basic"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        object.__setattr__(self, "auth_type", self.auth_type.strip().lower())
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        if self.timeout_seconds <= 0:
            raise GerritConfigError("GERRIT_TIMEOUT_SECONDS must be greater than zero.")
        if self.auth_type not in SUPPORTED_AUTH_TYPES:
            allowed = ", ".join(sorted(SUPPORTED_AUTH_TYPES))
            raise GerritConfigError(f"Unsupported GERRIT_AUTH_TYPE={self.auth_type!r}. Allowed: {allowed}.")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "GerritConfig":
        source = os.environ if env is None else env
        timeout_raw = source.get("GERRIT_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise GerritConfigError("GERRIT_TIMEOUT_SECONDS must be numeric.") from exc

        return cls(
            base_url=source.get("GERRIT_BASE_URL", ""),
            username=source.get("GERRIT_USERNAME"),
            http_password=source.get("GERRIT_HTTP_PASSWORD"),
            bearer_token=source.get("GERRIT_BEARER_TOKEN"),
            access_token=source.get("GERRIT_ACCESS_TOKEN"),
            cookie=source.get("GERRIT_COOKIE"),
            xsrf_token=source.get("GERRIT_XSRF_TOKEN"),
            auth_type=source.get("GERRIT_AUTH_TYPE", "basic"),
            timeout_seconds=timeout_seconds,
            verify_ssl=parse_bool(source.get("GERRIT_VERIFY_SSL"), default=True),
        )


def make_auth_provider(config: GerritConfig) -> AuthProvider:
    if config.auth_type == "basic":
        return BasicAuthProvider(config.username, config.http_password)
    if config.auth_type == "bearer":
        return BearerTokenProvider(config.bearer_token)
    if config.auth_type == "access_token":
        return AccessTokenProvider(config.access_token)
    if config.auth_type == "cookie_xsrf":
        return CookieXsrfProvider(config.cookie, config.xsrf_token)
    if config.auth_type == "anonymous":
        return AnonymousProvider()
    raise GerritConfigError(f"Unsupported GERRIT_AUTH_TYPE={config.auth_type!r}.")


class GerritClient:
    """Small standard-library Gerrit REST client."""

    def __init__(self, config: GerritConfig, auth_provider: Optional[AuthProvider] = None) -> None:
        self.config = config
        self.auth_provider = auth_provider or make_auth_provider(config)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "GerritClient":
        return cls(GerritConfig.from_env(env))

    def get(
        self,
        path: str,
        query: Optional[QueryParams] = None,
        headers: Optional[Mapping[str, str]] = None,
        authenticated: bool = True,
    ) -> GerritResponse:
        return self.request("GET", path, query=query, headers=headers, authenticated=authenticated)

    def post(
        self,
        path: str,
        query: Optional[QueryParams] = None,
        json_body: Any = None,
        data: Optional[Union[str, bytes]] = None,
        headers: Optional[Mapping[str, str]] = None,
        authenticated: bool = True,
    ) -> GerritResponse:
        return self.request(
            "POST",
            path,
            query=query,
            json_body=json_body,
            data=data,
            headers=headers,
            authenticated=authenticated,
        )

    def put(
        self,
        path: str,
        query: Optional[QueryParams] = None,
        json_body: Any = None,
        data: Optional[Union[str, bytes]] = None,
        headers: Optional[Mapping[str, str]] = None,
        authenticated: bool = True,
    ) -> GerritResponse:
        return self.request(
            "PUT",
            path,
            query=query,
            json_body=json_body,
            data=data,
            headers=headers,
            authenticated=authenticated,
        )

    def delete(
        self,
        path: str,
        query: Optional[QueryParams] = None,
        json_body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        authenticated: bool = True,
    ) -> GerritResponse:
        return self.request(
            "DELETE",
            path,
            query=query,
            json_body=json_body,
            headers=headers,
            authenticated=authenticated,
        )

    def version(self) -> GerritResponse:
        return self.get("/config/server/version", authenticated=False)

    def whoami(self) -> GerritResponse:
        return self.get("/accounts/self/detail", authenticated=True)

    def request(
        self,
        method: str,
        path: str,
        query: Optional[QueryParams] = None,
        json_body: Any = None,
        data: Optional[Union[str, bytes]] = None,
        headers: Optional[Mapping[str, str]] = None,
        authenticated: bool = True,
    ) -> GerritResponse:
        method = method.upper()
        if method not in ("GET", "POST", "PUT", "DELETE"):
            raise GerritConfigError(f"Unsupported HTTP method: {method}")
        if json_body is not None and data is not None:
            raise GerritConfigError("Use either json_body or data, not both.")

        request_headers = self._base_headers(headers)
        request_path, request_query = self.auth_provider.apply(
            normalize_request_path(path),
            query,
            request_headers,
            authenticated=authenticated,
        )
        url = self._url_for_path(request_path, request_query)
        payload = self._encode_body(json_body=json_body, data=data, headers=request_headers)
        req = request.Request(url, data=payload, headers=request_headers, method=method)

        try:
            with request.urlopen(
                req,
                timeout=self.config.timeout_seconds,
                context=self._ssl_context(),
            ) as resp:
                body = resp.read()
                return self._make_response(
                    method=method,
                    url=url,
                    status=resp.status,
                    reason=resp.reason,
                    headers=dict(resp.headers.items()),
                    body=body,
                    strict_json=True,
                )
        except urlerror.HTTPError as exc:
            body = exc.read()
            response = self._make_response(
                method=method,
                url=url,
                status=exc.code,
                reason=exc.reason,
                headers=dict(exc.headers.items()),
                body=body,
                strict_json=False,
            )
            raise GerritHTTPError(response, redactor=self.redact) from None
        except urlerror.URLError as exc:
            raise GerritTransportError(self.redact(f"{method} {url} failed: {exc}")) from None
        except TimeoutError as exc:
            raise GerritTransportError(self.redact(f"{method} {url} timed out: {exc}")) from None
        except OSError as exc:
            raise GerritTransportError(self.redact(f"{method} {url} failed: {exc}")) from None

    def build_url(
        self,
        path: str,
        query: Optional[QueryParams] = None,
        authenticated: bool = True,
    ) -> str:
        headers: Dict[str, str] = {}
        request_path, request_query = self.auth_provider.apply(
            normalize_request_path(path),
            query,
            headers,
            authenticated=authenticated,
        )
        return self._url_for_path(request_path, request_query)

    def _url_for_path(self, path: str, query: Optional[QueryParams]) -> str:
        url = self.config.base_url + path
        query_string = encode_query(query)
        return f"{url}?{query_string}" if query_string else url

    def redact(self, value: str) -> str:
        return redact_text(value, secrets=self.auth_provider.redaction_secrets())

    def _base_headers(self, headers: Optional[Mapping[str, str]]) -> Dict[str, str]:
        request_headers: Dict[str, str] = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        return request_headers

    def _encode_body(
        self,
        json_body: Any,
        data: Optional[Union[str, bytes]],
        headers: Dict[str, str],
    ) -> Optional[bytes]:
        if json_body is not None:
            headers.setdefault("Content-Type", JSON_CONTENT_TYPE)
            return json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        if data is None:
            return None
        if isinstance(data, bytes):
            return data
        return data.encode("utf-8")

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if not self.config.base_url.startswith("https://"):
            return None
        if self.config.verify_ssl:
            return ssl.create_default_context()
        return ssl._create_unverified_context()

    def _make_response(
        self,
        method: str,
        url: str,
        status: int,
        reason: str,
        headers: Mapping[str, str],
        body: bytes,
        strict_json: bool,
    ) -> GerritResponse:
        sanitized_headers = sanitize_headers(headers)
        content_type = _content_type(headers)
        text, data = decode_response_body(body, content_type, strict_json=strict_json)
        return GerritResponse(
            method=method,
            url=redact_url(url),
            status=status,
            reason=reason,
            headers=sanitized_headers,
            content_type=content_type,
            text=self.redact(text),
            data=data,
        )


def encode_query(query: Optional[QueryParams]) -> str:
    if not query:
        return ""
    return parse.urlencode(list(_iter_query_pairs(query)))


def decode_response_body(
    body: bytes,
    content_type: str,
    strict_json: bool = True,
) -> Tuple[str, Any]:
    if not body:
        return "", None

    text = body.decode(_charset(content_type), errors="replace")
    cleaned = strip_xssi_prefix(text)
    stripped = cleaned.strip()
    if not stripped:
        return cleaned, None

    should_parse_json = "application/json" in content_type.lower() or text.startswith(XSSI_PREFIX)
    if should_parse_json:
        try:
            return cleaned, json.loads(stripped)
        except json.JSONDecodeError as exc:
            if strict_json:
                raise GerritParseError(f"Invalid Gerrit JSON response: {exc}") from exc
            return cleaned, None

    return cleaned, cleaned


def _iter_query_pairs(query: QueryParams) -> Iterable[Tuple[str, str]]:
    if isinstance(query, Mapping):
        items = query.items()
    else:
        items = query

    for key, value in items:
        if value is None:
            continue
        if _is_sequence_value(value):
            for item in value:  # type: ignore[union-attr]
                if item is not None:
                    yield str(key), _query_value_to_string(item)
        else:
            yield str(key), _query_value_to_string(value)  # type: ignore[arg-type]


def _query_value_to_string(value: ScalarQueryValue) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _is_sequence_value(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in SENSITIVE_QUERY_KEYS or any(marker in lowered for marker in SENSITIVE_QUERY_KEYS)


def _content_type(headers: Mapping[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value
    return ""


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([^;]+)", content_type, flags=re.IGNORECASE)
    return match.group(1).strip() if match else "utf-8"


__all__ = [
    "AccessTokenProvider",
    "AnonymousProvider",
    "AuthProvider",
    "BasicAuthProvider",
    "BearerTokenProvider",
    "CookieXsrfProvider",
    "GerritClient",
    "GerritClientError",
    "GerritConfig",
    "GerritConfigError",
    "GerritHTTPError",
    "GerritParseError",
    "GerritResponse",
    "GerritTransportError",
    "decode_response_body",
    "encode_query",
    "make_basic_auth_header",
    "make_auth_provider",
    "normalize_base_url",
    "normalize_request_path",
    "quote_path_segment",
    "redact_text",
    "strip_xssi_prefix",
]
