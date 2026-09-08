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

    Do not use this for an email greeting: ``Hi x.arrieta,`` is not a
    greeting. Greeting call sites use ``greeting_name`` below (issue #1591).
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


# The single definition of the copy used when a recipient has no name on
# file. Roughly 7 in 10 accounts are in that state (issue #1591), so this
# string ships on real member mail, not just in edge cases.
GREETING_FALLBACK = 'there'


def greeting_name(user):
    """Real name for use in an email greeting, or ``''``.

    Sibling of :func:`display_name` with one deliberate difference: it never
    falls back to the email local-part. A card label must always render
    something identifying; a greeting must never render something that is not
    a name (``Hi x.arrieta,``). Callers pair it with
    ``or GREETING_FALLBACK``.

    Returns ``f"{first_name} {last_name}".strip()`` when either name is
    non-blank, otherwise ``''``. Whitespace-only names count as blank, and
    ``None`` returns ``''``. ``user.email`` is never inspected.
    """
    if user is None:
        return ''
    first = (getattr(user, 'first_name', '') or '').strip()
    last = (getattr(user, 'last_name', '') or '').strip()
    return f'{first} {last}'.strip()
