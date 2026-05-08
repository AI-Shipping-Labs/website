"""Tests for the custom User model.

All tests in this module previously exercised
``BaseUserManager`` semantics that Django's own test suite covers:
``create_user`` requiring an email, ``normalize_email``,
password storage, ``create_superuser`` flag enforcement, and
``unique=True`` on ``email``. Per ``_docs/testing-guidelines.md``
Rule 3 we don't re-test Django framework behaviour, so the
module is intentionally empty. Custom ``User`` behaviour we
own (tier transitions, gating, override resolution, etc.) is
covered in ``accounts/tests/test_tier_override.py`` and the
content/payments suites.
"""
