"""Tests for ``WebhookLog``.

Previously contained a ``test_ordering`` that exercised
``Meta.ordering`` — Django framework behaviour, removed per
``_docs/testing-guidelines.md`` Rule 3. ``WebhookLog`` ships
without custom behaviour worth covering at the model layer
today; webhook handlers exercise it via integration tests in
``payments/`` and ``integrations/``.
"""
