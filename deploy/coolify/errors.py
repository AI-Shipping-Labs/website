"""Safe deployment errors that never include response bodies or credentials."""


class ReleaseError(RuntimeError):
    """Fail-closed release validation or control-plane error."""
