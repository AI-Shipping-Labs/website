"""Conflict-safe user creation for public email entry points."""

from django.db import IntegrityError, transaction

from accounts.models import User


def create_user_conflict_safe(*, email, password=None, **extra_fields):
    """Create a user or return the row that won an email-uniqueness race.

    The inner ``atomic`` block is intentionally a savepoint when the caller is
    already in a transaction. Rolling it back keeps the connection usable for
    the winner lookup after ``create_user`` raises ``IntegrityError``.
    """
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=password,
                **extra_fields,
            )
    except IntegrityError as error:
        try:
            winner = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # An integrity failure without a row for this email came from a
            # different constraint and must retain its normal error semantics.
            raise error from None
        return winner, False

    return user, True
