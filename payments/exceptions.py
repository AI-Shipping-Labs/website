"""Custom exceptions for the payments app."""


class WebhookPermanentError(Exception):
    """Raised by a webhook handler when the event is malformed or otherwise
    not safe to retry.

    The webhook view records the event as ``failed_permanent`` and returns
    ``200`` so Stripe stops retrying. Generic ``Exception`` from a handler
    means "transient": the view returns ``500`` and records nothing, so
    Stripe's next delivery re-runs the handler.
    """


class WebhookUnmatchedUserError(Exception):
    """Raised when a subscription callback matches zero local users.

    Issue #1314. This is a RETRYABLE failure: webhook ordering may deliver a
    subscription event before local checkout fulfillment created the user, so
    the dispatcher records the delivery attempt as ``unmatched_user``, does
    NOT write a terminal ``WebhookEvent``, and returns 500 so Stripe retries.
    """

    def __init__(self, message, *, subscription_id="", customer_id=""):
        super().__init__(message)
        self.subscription_id = subscription_id
        self.customer_id = customer_id


class WebhookAmbiguousUserError(Exception):
    """Raised when a subscription callback matches more than one local user.

    Issue #1314. This is a TERMINAL failure that mutates nobody: retrying
    cannot safely select an owner. The dispatcher records the attempt as
    ``ambiguous_user``, writes a terminal ``WebhookEvent``, alerts operators,
    and returns 200 so Stripe stops retrying.
    """

    def __init__(self, message, *, matched_by="", subscription_id="",
                 customer_id="", user_ids=None):
        super().__init__(message)
        self.matched_by = matched_by
        self.subscription_id = subscription_id
        self.customer_id = customer_id
        self.user_ids = list(user_ids or [])
