"""Human-readable explanations for SES bounce/complaint events (issue #849).

Single source of truth for the operator-facing copy shown on the Studio SES
events list and detail pages. Three concerns live here:

1. Severity classification keyed off ``SesEvent.event_type`` — answers "how
   bad is this?" with a label, a reused pill colour, and a one-line
   consequence sentence.
2. A plain-English glossary for SES ``bounce_type`` / ``bounce_subtype``
   values so a staff member without SES expertise can read a row.
3. A decoder for SMTP enhanced status codes (RFC 3463 ``class.subject.detail``)
   found inside a ``diagnostic_code`` string.

This module is presentation-only. It does NOT re-parse the raw payload, does
NOT touch the bounce side-effect logic, and imports the soft-bounce threshold
from :mod:`accounts.utils.bounce` so the "3-strike rule" copy can never drift
from the number the webhook actually enforces.

The studio template tag library (``studio/templatetags/studio_filters.py``)
exposes thin wrappers over the functions here; templates carry no copy.
"""

import re

from accounts.utils.bounce import SOFT_BOUNCE_THRESHOLD
from email_app.models.ses_event import SesEvent

# --- Severity classification -------------------------------------------------

# Severity tiers. The pill class strings MUST match the existing palette in
# ``studio/views/ses_events.py`` (``EVENT_TYPE_PILL_CLASSES``) so the severity
# indicator reuses the same red / amber / neutral the Type pill already uses —
# no new colour values are introduced anywhere.
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_INFO = "info"

_SEVERITY_LABELS = {
    SEVERITY_HIGH: "Serious",
    SEVERITY_MEDIUM: "Temporary",
    SEVERITY_INFO: "Informational",
}

_SEVERITY_CLASSES = {
    SEVERITY_HIGH: "bg-red-500/20 text-red-400",
    SEVERITY_MEDIUM: "bg-amber-500/20 text-amber-300",
    SEVERITY_INFO: "bg-secondary text-muted-foreground",
}

_SEVERITY_CONSEQUENCES = {
    SEVERITY_HIGH: (
        "Recipient was unsubscribed / suppressed and will not receive "
        "further email."
    ),
    SEVERITY_MEDIUM: (
        "Temporary delivery failure. SES retries; the user is only "
        "unsubscribed if soft bounces reach the threshold."
    ),
    SEVERITY_INFO: "No deliverability problem.",
}

# Map each known ``event_type`` to a severity tier. Anything not listed here
# (an unknown / future event_type) falls back to ``info`` without raising.
_EVENT_TYPE_SEVERITY = {
    SesEvent.EVENT_TYPE_BOUNCE_PERMANENT: SEVERITY_HIGH,
    SesEvent.EVENT_TYPE_COMPLAINT: SEVERITY_HIGH,
    SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT: SEVERITY_MEDIUM,
    SesEvent.EVENT_TYPE_BOUNCE_OTHER: SEVERITY_MEDIUM,
    SesEvent.EVENT_TYPE_DELIVERY: SEVERITY_INFO,
    SesEvent.EVENT_TYPE_OPEN: SEVERITY_INFO,
    SesEvent.EVENT_TYPE_CLICK: SEVERITY_INFO,
    SesEvent.EVENT_TYPE_SUBSCRIPTION_CONFIRMATION: SEVERITY_INFO,
    SesEvent.EVENT_TYPE_UNSUBSCRIBE_CONFIRMATION: SEVERITY_INFO,
    SesEvent.EVENT_TYPE_OTHER: SEVERITY_INFO,
}


def severity_for_event_type(event_type):
    """Return the severity tier (``high`` / ``medium`` / ``info``).

    Unknown / future event types fall back to ``info`` (neutral) so a new
    ``EVENT_TYPE_*`` value never raises on these read-only pages.
    """
    return _EVENT_TYPE_SEVERITY.get(event_type, SEVERITY_INFO)


def severity_label(event_type):
    """Plain-English severity label (``Serious`` / ``Temporary`` / ``Informational``)."""
    return _SEVERITY_LABELS[severity_for_event_type(event_type)]


def severity_classes(event_type):
    """Reused pill class string for the severity tier."""
    return _SEVERITY_CLASSES[severity_for_event_type(event_type)]


def severity_consequence(event_type):
    """One-line plain-English consequence sentence for the severity tier."""
    return _SEVERITY_CONSEQUENCES[severity_for_event_type(event_type)]


# --- Detail consequence note -------------------------------------------------

# The longer note shown on the detail page. It always explains both the
# immediate (permanent / complaint) and the deferred (transient 3-strike)
# outcome so the reader understands the full lifecycle. The threshold number
# is interpolated from ``SOFT_BOUNCE_THRESHOLD`` — never hardcoded — so the
# copy and the webhook can't disagree.
def consequence_note(event_type):
    """Return the detail-page consequence note for the event's severity.

    The note is tailored to the severity but always names the soft-bounce
    threshold so a reader on any bounce understands the 3-strike rule.
    """
    severity = severity_for_event_type(event_type)
    if severity == SEVERITY_HIGH:
        return (
            "A permanent bounce or complaint unsubscribes and suppresses "
            "the recipient immediately — they will receive no further "
            "email. A transient (soft) bounce instead becomes permanent "
            f"and unsubscribes the user only after {SOFT_BOUNCE_THRESHOLD} "
            "occurrences."
        )
    if severity == SEVERITY_MEDIUM:
        return (
            "This is a temporary (soft) failure. SES retries delivery and "
            "the user stays subscribed for now; a soft bounce only becomes "
            "permanent and unsubscribes the user after "
            f"{SOFT_BOUNCE_THRESHOLD} occurrences. A permanent bounce or "
            "complaint, by contrast, unsubscribes the user immediately."
        )
    return (
        "This event has no deliverability impact and does not change the "
        "recipient's subscription status. For reference: a permanent bounce "
        "or complaint unsubscribes the user immediately, while a transient "
        f"(soft) bounce does so only after {SOFT_BOUNCE_THRESHOLD} occurrences."
    )


# --- Bounce-type / bounce-subtype glossary -----------------------------------

# Keyed exactly on the SES ``bounceType`` / ``bounceSubType`` (and complaint
# subtype) string values. Unknown keys return "" (the caller renders the raw
# value with no tooltip). Lookups are case-insensitive on the key.
_BOUNCE_TYPE_GLOSSARY = {
    "permanent": "Permanent — the address is permanently undeliverable.",
    "transient": "Transient — a temporary failure that SES will retry.",
    "undetermined": "Undetermined — SES could not classify the failure.",
}

_BOUNCE_SUBTYPE_GLOSSARY = {
    "general": "General — no specific reason was returned by the receiving server.",
    "noemail": "NoEmail — the recipient address does not exist.",
    "suppressed": (
        "Suppressed — the address is on the SES account-level suppression list."
    ),
    "onaccountsuppressionlist": (
        "OnAccountSuppressionList — the address is on the SES suppression list."
    ),
    "mailboxfull": "MailboxFull — the recipient mailbox is over quota.",
    "messagetoolarge": (
        "MessageTooLarge — the message exceeded the recipient's size limit."
    ),
    "contentrejected": (
        "ContentRejected — the receiving server rejected the message content."
    ),
    "attachmentrejected": "AttachmentRejected — an attachment was rejected.",
    # Complaint subtype.
    "abuse": "abuse — the recipient marked the email as spam.",
}


def explain_term(value):
    """Plain-English text for a ``bounce_type`` or ``bounce_subtype`` value.

    Looks the value up in both the bounce-type and bounce-subtype glossaries
    (case-insensitive). Returns "" for blank / unknown values so the caller
    renders the raw value with no tooltip and never shows "None"/"undefined".
    """
    if not value:
        return ""
    key = str(value).strip().lower()
    if not key:
        return ""
    return _BOUNCE_TYPE_GLOSSARY.get(key) or _BOUNCE_SUBTYPE_GLOSSARY.get(key) or ""


# --- Diagnostic-code decoder -------------------------------------------------

# RFC 3463 enhanced status codes (``class.subject.detail``) mapped to plain
# English. Only common, high-signal codes are listed; unknown tokens are
# skipped silently (no crash, no "None").
_DIAGNOSTIC_CODE_GLOSSARY = {
    "4.4.7": "message expired in the queue before it could be delivered",
    "4.4.1": "could not connect to the recipient's mail server",
    "5.1.1": "recipient address does not exist",
    "5.2.2": "recipient mailbox is full",
    "5.7.1": "delivery refused / blocked by the recipient server (often spam policy)",
    "5.3.4": "message too large for the recipient server",
    "4.2.2": "recipient mailbox is temporarily full",
}

# Matches an RFC 3463 enhanced status token: digit.digit.digit. The detail
# segment can be multi-digit (e.g. ``5.7.26``) but the keys we recognise are
# all single-digit; non-recognised tokens are simply dropped by the lookup.
_STATUS_CODE_RE = re.compile(r"\b(\d\.\d\.\d{1,3})\b")


def decode_diagnostic(diagnostic_code):
    """Return ``[(code, explanation), ...]`` for recognised SMTP status codes.

    Extracts every ``class.subject.detail`` token from ``diagnostic_code`` in
    order of appearance, keeps only those we have plain-English copy for, and
    de-duplicates while preserving first-seen order. Returns ``[]`` when the
    diagnostic is blank or contains no recognised code; the caller always
    still renders the raw diagnostic verbatim, so this is purely additive.
    """
    if not diagnostic_code:
        return []
    seen = set()
    pairs = []
    for code in _STATUS_CODE_RE.findall(str(diagnostic_code)):
        if code in seen:
            continue
        explanation = _DIAGNOSTIC_CODE_GLOSSARY.get(code)
        if explanation is None:
            continue
        seen.add(code)
        pairs.append((code, explanation))
    return pairs
