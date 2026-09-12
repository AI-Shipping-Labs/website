"""Email error types shared by the legacy service and the extracted helpers.

Extracted from ``email_app.services.email_service`` (A1.2 slice 3) so the
rendering and SES transport modules can raise them without importing the
legacy ``EmailService`` class in a cycle. The names remain importable from
their old home for compatibility; renaming them away from the
``EmailService`` prefix belongs to the final slice that deletes it.
"""


class EmailServiceError(Exception):
    """Raised when email sending fails."""


class EmailTransportOutcomeUnknown(EmailServiceError):
    """Raised when SES may have accepted a request before transport failed."""
