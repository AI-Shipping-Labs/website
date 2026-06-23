"""HMAC signing for outbound event-hook deliveries (issue #1070).

The signature scheme mirrors the inbound GitHub webhook verification at
``integrations/services/github_sync/client.py`` so the receiving Lambda
can verify with the same primitives:

    signature = "sha256=" + hmac_sha256(secret, f"{timestamp}.{raw_body}")

The unix timestamp is part of the SIGNED string (not just a header) so the
handler can reject replays outside a tolerance window. ``compute_signature``
returns the full header value (with the ``sha256=`` prefix).
"""

import hashlib
import hmac


def compute_signature(secret, timestamp, raw_body):
    """Return the ``X-AISL-Signature`` header value for ``raw_body``.

    Args:
        secret: The subscription's shared signing secret (str).
        timestamp: Unix seconds as an int or str.
        raw_body: The exact request body bytes/str that will be sent.
    """
    if isinstance(raw_body, bytes):
        body_text = raw_body.decode("utf-8")
    else:
        body_text = raw_body
    signed = f"{timestamp}.{body_text}".encode("utf-8")
    digest = hmac.new(
        secret.encode("utf-8"),
        signed,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"
