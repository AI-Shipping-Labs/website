"""Live LLM-judge scenario test set (issue #811).

A SEPARATE, opt-in pytest package that exercises the two shipped AI
callables -- ``questionnaires.onboarding_ai.run_onboarding_turn`` (#804)
and ``integrations.services.feedback_synthesis.synthesize_feedback``
(#805) -- against the REAL configured LLM provider, asserting plain-English
scenario criteria via an LLM judge built on the #799 service.

Isolation (see ``_docs/testing-guidelines.md``):

- Every test carries ``pytestmark = pytest.mark.live_judge``.
- The whole set is skipped (never errored, zero live calls) when
  ``integrations.services.llm.is_enabled()`` is False.
- It lives outside ``playwright_tests/`` and is not a Django ``TestCase``
  module, so neither CI leg (``manage.py test`` / ``pytest
  playwright_tests/``) collects it.
- Run on demand only via ``make test-judge``.
"""
