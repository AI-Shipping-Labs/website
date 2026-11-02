"""Helpers for linking to Stripe Dashboard objects."""


def stripe_dashboard_url(account_id, resource, object_id):
    """Build an account-scoped dashboard URL or return an empty string."""
    if not account_id or not object_id:
        return ""
    return (
        f"https://dashboard.stripe.com/{account_id}/{resource}/{object_id}"
    )
