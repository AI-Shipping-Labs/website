"""Custom exceptions for the payments app."""


class WebhookPermanentError(Exception):
    """Raised by a webhook handler when the event is malformed or otherwise
    not safe to retry.

    The webhook view records the event as ``failed_permanent`` and returns
    ``200`` so Stripe stops retrying. Generic ``Exception`` from a handler
    means "transient": the view returns ``500`` and records nothing, so
    Stripe's next delivery re-runs the handler.
    """
