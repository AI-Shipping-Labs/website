"""Structured errors shared by submission read and write services."""


class OnBehalfError(Exception):
    """A refusal the API maps onto its structured error envelope."""

    def __init__(self, message, code, *, status=422, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details or {}


def refuse_existing(existing_summary, kind):
    """Raise the stable conflict with a recovery path for the submission kind."""
    message = f'The member already has a {kind} submission; pass replace to overwrite it'
    if kind == 'homework':
        message = (
            'The member already has a homework submission; inspect it with '
            '`asl homework show` before passing replace'
        )
    raise OnBehalfError(
        message,
        'submission_exists', status=409,
        details={'existing_submission': existing_summary},
    )
