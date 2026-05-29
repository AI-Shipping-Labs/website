"""Flexible questionnaire system (issue #800).

The team runs free-form questionnaires with members (intake before a
plan, feedback after a sprint). The question set varies per purpose and
per respondent. This app is the generic foundation reused by the
onboarding (#801, #802, #804) and sprint-feedback (#803, #805) features.

This is a distinct system from ``voting/Poll`` (option-voting only, no
free text). Do NOT reuse ``Poll`` for free-form questionnaires.

Two layers:

- The authored template: ``Questionnaire`` -> ``Question`` ->
  ``QuestionOption``. The base question set staff author once.
- The per-respondent instance: ``Response`` -> ``ResponseQuestion`` ->
  ``ResponseQuestionOption`` -> ``Answer``. Each response materializes
  its own ordered question list (snapshotted from the base set via
  ``questionnaires.services.build_response_questions``) so per-respondent
  customization (#802) never mutates the shared template, and editing a
  base question later never silently rewrites questions a member has
  already started answering.
"""
