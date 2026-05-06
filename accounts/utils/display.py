"""Display-name helper for member-facing UI (issue #440).

Uses the user's first/last name when populated, otherwise falls back to
the email's local-part. Django's built-in ``User.get_full_name()``
returns an empty string when both names are blank, which is not useful
on cohort-board cards where we want at least the email handle.
"""


def display_name(user):
    """Best human label for a user.

    Order:
    1. ``f"{first_name} {last_name}".strip()`` if either is non-empty
    2. email local-part (the slice before ``@``)
    3. empty string for ``None`` / no email

    Whitespace-only first / last names count as empty so a profile with
    ``first_name='  '`` falls through to the email handle.
    """
    if user is None:
        return ''
    first = (getattr(user, 'first_name', '') or '').strip()
    last = (getattr(user, 'last_name', '') or '').strip()
    full = f'{first} {last}'.strip()
    if full:
        return full
    email = getattr(user, 'email', '') or ''
    if '@' in email:
        return email.split('@', 1)[0]
    return email
