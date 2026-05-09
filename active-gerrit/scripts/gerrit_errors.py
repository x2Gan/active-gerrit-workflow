#!/usr/bin/env python3
"""Error classification helpers for active-gerrit CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from gerrit_client import (
    GerritClientError,
    GerritConfigError,
    GerritHTTPError,
    GerritParseError,
    GerritTransportError,
)


@dataclass(frozen=True)
class ErrorDescriptor:
    type: str
    hint: Optional[str] = None
    status: Optional[int] = None


HTTP_ERROR_MAP = {
    400: ErrorDescriptor(
        type="GerritBadRequest",
        hint="Check request arguments, JSON payload, and Gerrit field names.",
        status=400,
    ),
    401: ErrorDescriptor(
        type="GerritAuthError",
        hint="Check GERRIT_USERNAME and GERRIT_HTTP_PASSWORD.",
        status=401,
    ),
    403: ErrorDescriptor(
        type="GerritPermissionError",
        hint="Check Gerrit project permission or capability for this operation.",
        status=403,
    ),
    404: ErrorDescriptor(
        type="GerritNotFound",
        hint="The resource does not exist or is not visible to the current user.",
        status=404,
    ),
    409: ErrorDescriptor(
        type="GerritConflict",
        hint="Refresh change state and resolve the Gerrit state conflict before retrying.",
        status=409,
    ),
    412: ErrorDescriptor(
        type="GerritPreconditionFailed",
        hint="Refresh the resource and verify required preconditions before retrying.",
        status=412,
    ),
}


def http_error_descriptor(status: int) -> ErrorDescriptor:
    return HTTP_ERROR_MAP.get(
        status,
        ErrorDescriptor(
            type="GerritHTTPError",
            hint="Check Gerrit response details and request arguments.",
            status=status,
        ),
    )


def transport_error_descriptor(message: object) -> ErrorDescriptor:
    normalized = str(message).lower()
    if "timed out" in normalized or "timeout" in normalized:
        return ErrorDescriptor(
            type="GerritTimeoutError",
            hint="Check network reachability or increase GERRIT_TIMEOUT_SECONDS if the Gerrit server is slow.",
        )
    if any(token in normalized for token in ("certificate verify failed", "hostname mismatch", "ssl", "tls", "x509")):
        return ErrorDescriptor(
            type="GerritTLSError",
            hint="Check Gerrit TLS certificates, hostname matching, or GERRIT_VERIFY_SSL for non-production test environments.",
        )
    if any(
        token in normalized
        for token in (
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
            "failed to resolve",
            "dns",
        )
    ):
        return ErrorDescriptor(
            type="GerritDNSError",
            hint="Check the Gerrit hostname, DNS resolution, and local network configuration.",
        )
    if any(
        token in normalized
        for token in (
            "connection refused",
            "connection reset",
            "connection aborted",
            "network is unreachable",
            "no route to host",
            "remote end closed connection",
        )
    ):
        return ErrorDescriptor(
            type="GerritConnectionError",
            hint="Check Gerrit service reachability, proxy settings, and whether the remote endpoint is accepting connections.",
        )
    return ErrorDescriptor(
        type="TransportError",
        hint="Check Gerrit network reachability, proxy settings, and TLS configuration.",
    )


def describe_exception(exc: BaseException) -> ErrorDescriptor:
    if isinstance(exc, GerritHTTPError):
        return http_error_descriptor(exc.response.status)
    if isinstance(exc, GerritTransportError):
        return transport_error_descriptor(exc)
    if isinstance(exc, GerritConfigError):
        return ErrorDescriptor(
            type="ConfigError",
            hint="Fix Gerrit environment configuration and rerun doctor.",
        )
    if isinstance(exc, GerritParseError):
        return ErrorDescriptor(
            type="ParseError",
            hint="Check whether the Gerrit response shape changed or the request options are incomplete.",
        )
    if isinstance(exc, GerritClientError):
        return ErrorDescriptor(
            type=type(exc).__name__,
            hint="Check Gerrit response details and local configuration.",
        )
    return ErrorDescriptor(type="UnexpectedError")