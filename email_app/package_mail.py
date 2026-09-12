"""Site defaults for sends through ``community_base.mail`` (plan issue A1.2).

The package owns the durable delivery and the worker; this helper layers the
transport decisions the old ``EmailService._send_ses`` made at call time onto
the package's ``send``: the per-type sender, the welcome Reply-To, the SES
configuration set that the site-owned SNS event ingress is keyed on, and
cc/bcc passthrough. Callers keep passing ``dedupe_key`` semantics through
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
from email_app.services.email_service import (
    DEFAULT_WELCOME_REPLY_TO_EMAIL,
    WELCOME_REPLY_TO_KEY,
)
from integrations.config import (
    get_config,
    validate_email_config_value,
)


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
):
    """Send one transactional mail through the package (A1.2).

    Returns the durable ``EmailDelivery`` (state ``suppressed`` when the
    preference resolver opted the recipient out — the old path returned
    ``None`` for the same case). SES transport outcomes land on the delivery
    from the worker; a transport failure no longer raises into the caller.
    ``related`` takes a saved model instance and lands on the delivery as
    its ``related_object_type``/``related_object_id`` pair; the site
    recorder maps an ``events.event`` relation onto the ``EmailLog`` audit
    row's event FK.
    """

    context = context or {}
    # Issue #1613: nothing durable may exist yet when the guard fires, so
    # a URL-bearing context is refused before any row or job is created.
    ensure_no_rendered_urls(template_name, context)
    to_email = (recipient_email or getattr(user, "email", "") or "").strip()
    key = idempotency_key or f"{template_name}:{uuid4().hex}"

    extra = {}
    if cc is not None:
        extra["cc"] = cc
    if bcc is not None:
        extra["bcc"] = bcc
    configuration_set = get_config("SES_CONFIGURATION_SET_NAME", "")
    if configuration_set and configuration_set.strip():
        extra["configuration_set"] = configuration_set.strip()
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

    with transaction.atomic():
        return package_send(
            purpose=template_name,
            to=to_email,
            context=context,
            idempotency_key=key,
            user=user if isinstance(user, Model) and getattr(user, "pk", None) else None,
            sender=get_sender_for_email_type(template_name),
            extra=extra or None,
            related=related,
        )
