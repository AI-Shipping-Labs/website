"""Context processors for the accounts app.

Issue #698: Globalizes the unverified-email banner. The banner partial
(`templates/includes/_unverified_email_banner.html`) reads
``latest_verification_email`` to surface the timestamp of the most recent
verification email. This context processor populates that key on every
template render, but only does the ``EmailLog`` query when the user is
authenticated AND unverified -- anonymous and verified users pay one
attribute read and return ``{}`` (no DB hit).
"""

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

    latest_verification_email = (
        EmailLog.objects
        .filter(user=user, email_type="email_verification")
        .order_by("-sent_at")
        .first()
    )
    return {"latest_verification_email": latest_verification_email}
