"""Site defaults for sends through ``community_base.mail`` (plan issue A1.2).

The package owns the durable delivery and the worker; this helper layers the
transport decisions the old ``EmailService`` made at call time onto the
package's ``send``: the per-type sender, the welcome Reply-To, and cc/bcc
passthrough. Callers keep passing ``dedupe_key`` semantics through
``idempotency_key``; without one, every call is its own logical send (a
password-reset request sends a new mail each time, as before).
"""

from __future__ import annotations

from uuid import uuid4

from community_base.mail import send as package_send
from django.db import transaction
from django.db.models import Model

from email_app.services.context_guard import ensure_no_rendered_urls
from email_app.services.email_classification import (
    WELCOME_EMAIL_TYPES,
    get_sender_for_email_type,
)
from integrations.config import (
    get_config,
    validate_email_config_value,
)

WELCOME_REPLY_TO_KEY = "RELAY_WELCOME_REPLY_TO_EMAIL"
DEFAULT_WELCOME_REPLY_TO_EMAIL = "welcome@aishippinglabs.com"


def send_package_mail(
    user,
    template_name,
    context=None,
    *,
    recipient_email=None,
    cc=None,
    bcc=None,
    idempotency_key=None,
    related=None,
    category=None,
):
    """Create a durable Relay delivery with site sender and welcome reply settings."""
    context = context or {}
    ensure_no_rendered_urls(template_name, context)
    to_email = (recipient_email or getattr(user, "email", "") or "").strip()
    key = idempotency_key or f"{template_name}:{uuid4().hex}"
    sender = _relay_sender(template_name)
    extra = _mail_extra(template_name, cc, bcc)
    with transaction.atomic():
        return package_send(
            purpose=template_name, to=to_email, context=context, idempotency_key=key,
            user=_saved_user(user), sender=sender,
            extra=extra, related=related, category=category,
        )


def _saved_user(user):
    if isinstance(user, Model) and getattr(user, "pk", None):
        return user
    return None


def _relay_sender(template_name):
    sender = get_sender_for_email_type(template_name)
    if "@" in sender:
        return sender.split("@")[0]
    return sender


def _mail_extra(template_name, cc, bcc):
    extra = {}
    if cc is not None:
        extra["cc"] = cc
    if bcc is not None:
        extra["bcc"] = bcc
    if template_name in WELCOME_EMAIL_TYPES:
        reply_to = validate_email_config_value(
            WELCOME_REPLY_TO_KEY,
            get_config(
                WELCOME_REPLY_TO_KEY,
                DEFAULT_WELCOME_REPLY_TO_EMAIL,
            ),
        )
        if reply_to:
            extra["reply_to"] = [reply_to]

    return extra or None
