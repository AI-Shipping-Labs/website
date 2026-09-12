"""Durable-context URL guard for sends through the site mail boundary.

Issue #1613: an ``EmailDelivery`` row is durable and its context is
retained across retries, so no rendered link may travel in it. The guard
runs in ``send_package_mail`` before anything durable exists and
recursively rejects URL-bearing strings; the worker
(``email_app.hooks.resolve_auth_mail_context``) mints every rendered URL
afterwards from non-secret inputs. Error messages name the purpose and
the context path only, never the rejected value.
"""

from __future__ import annotations

import re

from community_base.mail.service import MailError

# Absolute URIs, including one embedded in prose (https://, s3://, ...).
ABSOLUTE_URI_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://")
# URI schemes that do not use a ``//`` authority.
SCHEMELESS_URI_RE = re.compile(
    r"\b(?:mailto|tel|urn|data|javascript|bitcoin):",
    re.IGNORECASE,
)
# Bearer-looking query parameters on relative links.
BEARER_PARAM_RE = re.compile(
    r"[?&](?:token|code|key|signature|access_token)=",
    re.IGNORECASE,
)
# Signed AWS query parameters (X-Amz-Signature and friends).
AMZ_PARAM_RE = re.compile(r"x-amz-", re.IGNORECASE)

# Sanitized same-site navigation input the worker signs into tokens; it
# is never rendered, so it is not a stored link.
RESOLVER_INPUT_KEYS = frozenset({"return_path"})


def looks_like_url(value):
    if not isinstance(value, str):
        return False
    return bool(
        ABSOLUTE_URI_RE.search(value)
        or value.startswith("//")
        or SCHEMELESS_URI_RE.search(value)
        or BEARER_PARAM_RE.search(value)
        or AMZ_PARAM_RE.search(value)
    )


def _walk(value, path, violations):
    if isinstance(value, dict):
        for key, item in value.items():
            key_str = str(key)
            child_path = f"{path}.{key_str}" if path else key_str
            if not path and key_str in RESOLVER_INPUT_KEYS:
                continue
            _walk(item, child_path, violations)
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", violations)
    elif looks_like_url(value):
        violations.append(path or "<root>")


def ensure_no_rendered_urls(purpose, context):
    """Raise ``MailError`` when any durable context value carries a URL.

    The exception identifies the purpose and the first offending context
    path so a caller can fix the send; the rejected value itself never
    appears in the message, a log, or the durable row.
    """

    violations = []
    _walk(context, "", violations)
    if violations:
        raise MailError(
            "durable mail context stores a URL: "
            f"purpose={purpose} path={violations[0]}"
        )
