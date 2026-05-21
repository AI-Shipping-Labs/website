"""Context processors for the accounts app.

Issue #698: Globalizes the unverified-email banner. The banner partial
(`templates/includes/_unverified_email_banner.html`) reads
``latest_verification_email`` to surface the timestamp of the most recent
verification email. This context processor populates that key on every
template render, but only does the ``EmailLog`` query when the user is
authenticated AND unverified -- anonymous and verified users pay one
attribute read and return ``{}`` (no DB hit).

Issue #769: Adds ``newsletter_only_user`` which exposes
``is_newsletter_only`` to every template so header / dashboard /
account page can branch on the gating predicate without a custom
``{% load %}``.
"""

from accounts.gating import is_newsletter_only_user
from email_app.models import EmailLog


def unverified_email_banner(request):
    """Expose ``latest_verification_email`` to all templates.

    Returns:
        ``{}`` when ``request.user`` is anonymous OR ``email_verified`` is
        True. The partial's ``{% if %}`` guard will not render anything in
        that case, so the missing context key is intentional and cheap.

        ``{"latest_verification_email": <EmailLog | None>}`` when the user
        is authenticated and unverified. Mirrors the query previously
        embedded in ``_render_account_page`` so the banner can display
        ``Last sent <timestamp>`` when a prior verification email exists.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    if user.email_verified:
        return {}

    # Issue #767: verification template was split into per-flow slugs
    # (signup vs subscribe). The banner aggregates across both so it
    # surfaces the latest verification send regardless of which path
    # the user came in on.
    latest_verification_email = (
        EmailLog.objects
        .filter(
            user=user,
            email_type__in=[
                "email_verification_signup",
                "email_verification_subscribe",
            ],
        )
        .order_by("-sent_at")
        .first()
    )
    return {"latest_verification_email": latest_verification_email}


def newsletter_only_user(request):
    """Expose ``is_newsletter_only`` to every template (issue #769).

    The flag is consumed by ``templates/includes/header.html`` (to hide
    the notification bell + Profile/Plan menu items), by
    ``templates/accounts/account.html`` (to trim cards on /account/),
    and by ``content/views/home.py::home`` (to redirect ``/`` to the
    trimmed account page). One field read per request — no DB hit.
    """
    return {
        "is_newsletter_only": is_newsletter_only_user(
            getattr(request, "user", None)
        ),
    }
