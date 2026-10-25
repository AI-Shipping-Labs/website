"""Build Relay campaign payloads without coupling them to HTTP handling."""

from typing import Any


def build_campaign_payload(
    recipient_email: str,
    template_key: str,
    template_version: int,
    idempotency_key: str,
    category: str,
    context: dict[str, str],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Return one Relay payload with only populated optional fields."""
    payload = {
        "email": recipient_email.strip(),
        "template_key": template_key,
        "template_version": template_version,
        "idempotency_key": idempotency_key,
        "context": context,
        "category": category,
    }
    for name in ("headers", "metadata", "cc", "bcc", "from_email"):
        value = options.get(name)
        if value:
            payload[name] = value
    if options.get("dry_run"):
        payload["dry_run"] = True
    return payload
