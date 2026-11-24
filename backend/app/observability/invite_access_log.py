"""Redact invite tokens from uvicorn access logs.

Path routes stay for already-shipped clients. This filter replaces the token
segment and any ``token`` query parameter before the line is emitted. It does
not rewrite ALB or CloudWatch request lines.
"""

from __future__ import annotations

import logging
import re


_TOKEN_PATH = re.compile(
    r"(/api/invites/)([^/?\s]+)(/join|/account-join)?",
    re.IGNORECASE,
)
_TOKEN_QUERY = re.compile(r"([?&]token=)[^&\s\"']+", re.IGNORECASE)
_STATIC_SEGMENTS = frozenset({"preview", "join", "account-join"})
_INSTALLED = False


def redact_invite_access_text(message: str) -> str:
    def _path(match: re.Match[str]) -> str:
        segment = match.group(2)
        suffix = match.group(3) or ""
        if segment.lower() in _STATIC_SEGMENTS and not suffix:
            return match.group(0)
        return f"{match.group(1)}<redacted>{suffix}"

    redacted = _TOKEN_PATH.sub(_path, message)
    return _TOKEN_QUERY.sub(r"\1<redacted>", redacted)


class InviteAccessLogRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Leave printf placeholders in the format string. Uvicorn puts the
        # request line in an argument, and rewriting both sides drops the
        # placeholder before formatting.
        if isinstance(record.msg, str) and not record.args:
            record.msg = redact_invite_access_text(record.msg)
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            record.args = tuple(
                redact_invite_access_text(arg) if isinstance(arg, str) else arg
                for arg in args
            )
        return True


def install_invite_access_log_redaction() -> None:
    """Attach the filter once. Safe to call from application import."""

    global _INSTALLED
    if _INSTALLED:
        return
    redaction = InviteAccessLogRedactionFilter()
    for name in ("uvicorn.access", "uvicorn"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, InviteAccessLogRedactionFilter) for item in logger.filters):
            logger.addFilter(redaction)
    _INSTALLED = True
