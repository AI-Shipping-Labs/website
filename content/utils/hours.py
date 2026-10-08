"""Canonical display formatting for learner-entered hour values (issue #1923).

Homework time spent is stored as a nullable float. The stepper echoes the
stored value back into the review form and the accepted-submission rows, so
the text must read the way a learner would type it: ``0`` not ``0.0``, ``2``
not ``2.0``, ``1.5`` as ``1.5``, and never exponent notation.
"""

import math
from decimal import Decimal, InvalidOperation


def format_hours(value):
    """Format a stored hour value; ``None`` (not provided) stays ``''``.

    ``str(float)`` yields the shortest round-tripping repr, so ``Decimal`` of
    it preserves exactly what was stored without binary-float noise.
    """
    if value is None:
        return ''
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError(f'Hour value must be finite, got {value!r}')
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), 'f')


def normalize_hours_text(raw):
    """Rewrite a numeric hour string into the canonical format.

    Used for stored draft strings. Anything that is not a finite,
    non-negative number (blank, typos, negatives, non-strings) is returned
    unchanged so in-progress learner input is never destroyed.
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw
    try:
        number = Decimal(raw.strip())
    except InvalidOperation:
        return raw
    if not number.is_finite() or number < 0:
        return raw
    # Match ``_parse_optional_hours``: only values that survive the float the
    # submit path would store are rewritten. Huge exponents ("1e400",
    # "1e999999999") overflow to inf and stay untouched, so the integer
    # formatting below never meets an unbounded value.
    try:
        hours = float(number)
    except (OverflowError, ValueError):
        return raw
    if not math.isfinite(hours):
        return raw
    return format_hours(hours)
