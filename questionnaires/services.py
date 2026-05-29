"""Service helpers for the questionnaire system (issue #800).

``build_response_questions`` is the shared seam #802 (per-member
customization) and #803 (bulk feedback responses) both call to
materialize a response's question set from the questionnaire's base
question set. It is pure-Python and ORM-only -- no HTTP, no AI.
"""

from questionnaires.models import (
    Answer,  # noqa: F401  (re-exported convenience for dependent apps)
    ResponseQuestion,
    ResponseQuestionOption,
)


def build_response_questions(response):
    """Copy base questions + options onto ``response`` as snapshots.

    For a freshly created :class:`~questionnaires.models.Response`, copy
    every base :class:`~questionnaires.models.Question` (and its
    :class:`~questionnaires.models.QuestionOption` rows) into
    :class:`~questionnaires.models.ResponseQuestion` /
    :class:`~questionnaires.models.ResponseQuestionOption` rows.

    Idempotent: if the response already has any response-questions this is
    a no-op and returns the empty list, so re-running never duplicates
    rows. Returns the list of created ``ResponseQuestion`` instances.

    Snapshotting (not live FK reads) means a later edit to a base question
    never silently rewrites a response a member has already started.
    """
    if response.response_questions.exists():
        return []

    base_questions = response.questionnaire.questions.all()
    created = []
    for base in base_questions:
        rq = ResponseQuestion.objects.create(
            response=response,
            source_question=base,
            question_type=base.question_type,
            prompt=base.prompt,
            help_text=base.help_text,
            is_required=base.is_required,
            order=base.order,
            scale_min=base.scale_min,
            scale_max=base.scale_max,
        )
        for opt in base.options.all():
            ResponseQuestionOption.objects.create(
                response_question=rq,
                source_option=opt,
                label=opt.label,
                order=opt.order,
            )
        created.append(rq)
    return created
