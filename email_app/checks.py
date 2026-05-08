"""Django system checks for the ``email_app``.

Issue #521 — surface a deploy-blocking error when a production-like
environment boots without ``SES_ENABLED=true``. Issue #509 introduced the
SES kill-switch (off by default everywhere except prod, where the
operator must opt in) and an operator who forgets to flip the switch in
production silently breaks every transactional email path: account
verification, password reset, newsletter double opt-in, event
registration confirmations, and campaign sends. The user sees a 200
response and the standard "check your email" UI but never gets the
email; the only signal in the logs is a single ``logger.info("SES
disabled - skipping send ...")`` line per send, which is too quiet to
catch in a routine deploy.

The check below converts that silent misconfiguration into a loud
``manage.py check`` failure: ``Error`` level is high enough to make
``manage.py check`` exit non-zero (so a deploy pipeline that runs
``manage.py check`` as a pre-flight step will block before the new
container is promoted) but does not stop ``runserver`` / ``migrate``
from running, which means a container that has already started keeps
serving the rest of the site even if email is broken.
"""

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security)
def check_ses_enabled_in_production(app_configs, **kwargs):
    """Refuse a production-like deploy when SES is disabled.

    Returns a single ``email_app.E001`` error when ``DEBUG=False`` and
    ``SES_ENABLED`` is missing or ``False``. ``DEBUG=True`` and the
    ``TESTING`` flag (set by ``website/settings.py`` when ``manage.py
    test`` is on the command line) both skip the check entirely so
    local dev and the test runner stay silent. Django's test runner
    flips ``settings.DEBUG`` to ``False`` before running system
    checks, so the ``TESTING`` short-circuit is what keeps
    ``manage.py test`` quiet — without it every test invocation would
    fail. ``SES_ENABLED`` defaults to ``False`` if the attribute is
    missing — same behaviour as the settings-level kill-switch.
    """
    if getattr(settings, "TESTING", False):
        return []
    if getattr(settings, "DEBUG", False):
        return []
    if getattr(settings, "SES_ENABLED", False):
        return []
    return [
        Error(
            (
                "SES is disabled in a production-like environment "
                "(DEBUG=False, SES_ENABLED=False). Transactional email "
                "(registration, password reset, newsletter, event "
                "confirmations) will silently no-op. "
                "Set the env var SES_ENABLED=true in your deploy environment."
            ),
            hint=(
                "See _docs/configuration.md section 5 (Email (Amazon SES)) "
                "for the full list of SES env vars. To intentionally run a "
                "production-like environment without SES (for example a "
                "staging box that does not send real mail), silence this "
                "check with SILENCED_SYSTEM_CHECKS = ['email_app.E001'] in "
                "your settings."
            ),
            id="email_app.E001",
        )
    ]
