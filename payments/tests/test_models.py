"""Tests for the ``StripePaymentLink`` model.

The previous ``test_different_period_allowed`` round-tripped
``StripePaymentLink`` rows with different ``billing_period``
values to confirm Django can save two different rows — pure
ORM behaviour, not project logic. Removed per
``_docs/testing-guidelines.md`` Rule 3. Stripe-specific
behaviour for these links is exercised by the checkout/webhook
integration tests in ``payments/tests/`` proper.
"""
