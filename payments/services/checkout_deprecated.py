"""Hard-deprecated local checkout-session creation.

Paid signup now goes through configured Stripe Payment Links. This stub
remains only so stale internal callers fail loudly instead of silently
creating a half-broken session. Kept in its own module so it doesn't
pollute the active surface of :mod:`payments.services`.
"""


def create_checkout_session(user, tier_slug, billing_period, success_url, cancel_url):
    """Deprecated local Checkout Session creation.

    Paid signup now goes through configured Stripe Payment Links. This
    function remains only to fail loudly for stale internal callers.
    """
    raise RuntimeError(
        "Local Checkout Session creation is deprecated; use Stripe Payment Links."
    )
