"""Homework submission models — issue #1683, tranche 1.

Field names, choice values, and constraints mirror
``community_base.coursework`` (`DataTalksClub/community-base`) exactly,
except where documented on the issue, so a later package adoption is a
mapping rather than a rewrite. Two deliberate divergences worth remembering
when reading this file:

- ``Question.correct_answer`` is plaintext, not the donor's encrypted
  envelope (the content repo is already private; the actual leak surface is
  rendering, not storage -- enforced by never putting ``correct_answer`` /
  ``Answer.is_correct`` in a student-facing context, not by crypto).
- ``Homework.is_accepting_submissions`` gates on ``due_date`` when one is
  set, in addition to ``state``. A missing deadline leaves open homework
  accepting submissions until an operator changes its state.

Tester-confirmed bug fix (issue #1683 follow-up): ``content_id`` does NOT
use ``SyncedContentIdentityMixin`` here, unlike ``Unit``/``Course``. That
mixin's ``content_id`` is globally ``unique=True`` -- correct for a
curriculum row that exists exactly once, wrong for ``Homework``, which
exists once PER COHORT for the same curriculum unit. A second cohort
reusing the same homework unit would collide on that global uniqueness
and silently lose its ``Homework`` row (the per-file sync try/except
swallows the ``IntegrityError``). ``content_id`` is a plain field here;
uniqueness is scoped to ``(cohort, content_id)`` in ``Meta.constraints``
instead.
"""

from django.conf import settings
from django.db import models
from django.db.models import Value
from django.utils import timezone

from content.models.cohort import COHORT_MODE_SELF_PACED
from content.models.mixins import SourceMetadataMixin

MAX_LEARNING_IN_PUBLIC_LINKS = 20


class HomeworkState(models.TextChoices):
    CLOSED = 'CL', 'Closed'
    OPEN = 'OP', 'Open'
    SCORED = 'SC', 'Scored'


class Homework(SourceMetadataMixin, models.Model):
    """One cohort's homework assignment.

    Synced from a homework unit's ``questions:`` and optional ``due_date:`` frontmatter
    (see ``content/sync_parsers/families/homework.py``). ``content_id``
    mirrors the owning ``Unit``'s ``content_id`` -- resolution at render
    time is ``Homework.objects.filter(content_id=unit.content_id,
    cohort=...)``, never a stored FK on ``Unit`` (a stored FK would embed
    one cohort's homework into curriculum every cohort shares).

    ``content_id`` is deliberately NOT globally unique (see the module
    docstring) -- the same curriculum unit backs one ``Homework`` row per
    cohort that reuses it, so uniqueness is scoped to ``(cohort,
    content_id)``.
    """

    content_id = models.UUIDField(
        null=True, blank=True,
        help_text=(
            "Stable UUID from the owning Unit's frontmatter. NOT globally "
            "unique -- see the module docstring: scoped to (cohort, "
            "content_id) so multiple cohorts can each carry homework for "
            "the same curriculum unit."
        ),
    )
    slug = models.SlugField(max_length=300)
    cohort = models.ForeignKey(
        'content.Cohort', on_delete=models.CASCADE, related_name='homeworks',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='', db_default='')
    due_date = models.DateTimeField(null=True, blank=True)
    # Activated only after the source has approved question keys. Older
    # homework keeps its all-in-one submission form.
    stepper_enabled = models.BooleanField(default=False, db_default=False)
    # Source-authored final-form settings. A public-links cap of zero removes
    # the Learning in Public stop from the stepper.
    learning_in_public_cap = models.PositiveSmallIntegerField(default=0, db_default=0)
    homework_url_field = models.BooleanField(default=True, db_default=True)
    time_spent_lectures_field = models.BooleanField(default=False, db_default=False)
    time_spent_homework_field = models.BooleanField(default=False, db_default=False)
    state = models.CharField(
        max_length=2, choices=HomeworkState.choices,
        default=HomeworkState.OPEN, db_default=HomeworkState.OPEN,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['cohort', 'slug'],
                name='content_homework_cohort_slug_uq',
            ),
            models.UniqueConstraint(
                fields=['cohort', 'content_id'],
                name='content_homework_cohort_content_id_uq',
            ),
        ]

    def __str__(self):
        return f'{self.cohort} - {self.title}'

    @property
    def is_accepting_submissions(self):
        """True while ``state == OPEN`` and any assigned deadline remains.

        Deliberate divergence from the donor (issue #1683): the donor gates
        acceptance on ``state`` alone (an operator manually flips
        ``CLOSED``). Tranche 1 ships no Studio surface to do that, so
        without this automatic ``due_date`` condition on top,
        "deadline enforcement" -- on the owner's must-land list -- would
        never actually trigger.

        A missing ``due_date`` also means there is no deadline to enforce,
        for either cohort or self-paced learners. In both cases, only an
        operator changing ``state`` away from ``OPEN`` closes submissions.
        """
        if self.state != HomeworkState.OPEN:
            return False
        if self.cohort.mode == COHORT_MODE_SELF_PACED:
            return True
        return self.due_date is None or timezone.now() <= self.due_date

    @property
    def is_past_due(self):
        """True when an assigned deadline has passed.

        Always ``False`` for a self-paced cohort or homework without a
        deadline -- see ``is_accepting_submissions``.
        """
        if self.cohort.mode == COHORT_MODE_SELF_PACED or self.due_date is None:
            return False
        return timezone.now() > self.due_date

    @property
    def is_self_paced(self):
        """True for a ``mode='self_paced'`` cohort's homework.

        Tester-confirmed copy bug (issue #1683 follow-up): a self-paced
        homework closed via ``state`` was telling the student "the deadline
        passed on <date>" -- false, since self-paced homework is never
        deadline-gated (see ``is_accepting_submissions``). Callers building
        the closed-state message must branch on this instead of assuming
        every closed homework has a deadline reason.
        """
        return self.cohort.mode == COHORT_MODE_SELF_PACED


class QuestionType(models.TextChoices):
    MULTIPLE_CHOICE = 'MC', 'Multiple choice'
    FREE_FORM = 'FF', 'Free form'
    FREE_FORM_LONG = 'FL', 'Free form long'
    CHECKBOXES = 'CB', 'Checkboxes'


class AnswerType(models.TextChoices):
    ANY = 'ANY', 'Any'
    FLOAT = 'FLT', 'Float'
    INTEGER = 'INT', 'Integer'
    EXACT_STRING = 'EXS', 'Exact string'
    CONTAINS_STRING = 'CTS', 'Contains string'


class Question(SourceMetadataMixin, models.Model):
    """One question on a ``Homework``.

    ``correct_answer`` is a 1-based option index (comma-separated for
    checkboxes) for ``MULTIPLE_CHOICE``/``CHECKBOXES``, or the plain
    expected value for a typed ``FREE_FORM``/``FREE_FORM_LONG`` question.
    Never rendered to a non-staff request -- see the module docstring.
    """

    homework = models.ForeignKey(
        Homework, on_delete=models.CASCADE, related_name='questions',
    )
    # Frontmatter-authored stable id (``questions[].id``), matches the
    # donor field name exactly. This is the re-sync upsert key within a
    # homework: ``(homework, source_question_id)``.
    source_question_id = models.SlugField(
        max_length=128, blank=True, default='', db_default='',
    )
    text = models.TextField()
    question_type = models.CharField(max_length=2, choices=QuestionType.choices)
    answer_type = models.CharField(
        max_length=3, choices=AnswerType.choices, blank=True, default='', db_default='',
    )
    # Newline-delimited option list for MULTIPLE_CHOICE/CHECKBOXES.
    possible_answers = models.TextField(blank=True, default='', db_default='')
    correct_answer = models.TextField(blank=True, default='', db_default='')
    scores_for_correct_answer = models.IntegerField(default=1, db_default=1)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.homework} - {self.text[:50]}'

    @property
    def options_list(self):
        """``possible_answers`` split into a list, in author order."""
        return [line for line in self.possible_answers.splitlines() if line.strip()]


class Submission(models.Model):
    """One student's submission for a ``Homework``.

    One row per ``(homework, student)`` -- get-or-created on submit, always
    updated in place on re-submit, never a second row.
    """

    homework = models.ForeignKey(
        Homework, on_delete=models.CASCADE, related_name='submissions',
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='homework_submissions',
    )
    # community_base.curriculum.Enrollment (user+cohort) is what the donor's
    # Submission.enrollment actually points to -- CohortEnrollment is this
    # repo's matching shape, not the course-level content.Enrollment.
    # Auto-created on first submit (see content/services/homework_submissions.py)
    # so a missing separate "enroll in the cohort" step can never block a
    # submission.
    enrollment = models.ForeignKey(
        'content.CohortEnrollment', on_delete=models.CASCADE,
        related_name='homework_submissions',
    )
    homework_link = models.URLField(blank=True, null=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    questions_score = models.IntegerField(default=0, db_default=0)
    total_score = models.IntegerField(default=0, db_default=0)
    learning_in_public_links = models.JSONField(
        default=list, blank=True, null=True,
        help_text='Optional public links submitted with this homework.',
    )
    time_spent_lectures = models.FloatField(null=True, blank=True)
    time_spent_homework = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['homework', 'student'],
                name='content_submission_homework_student_uq',
            ),
        ]
        ordering = ['-submitted_at']

    def __str__(self):
        return f'{self.student} - {self.homework}'


class Answer(models.Model):
    """One answered question within a ``Submission``.

    Only written for questions the student actually answered -- a blank
    question simply has no ``Answer`` row (see the "Submission lifecycle"
    section of issue #1683: partial submissions must always succeed).
    """

    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name='answers',
    )
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name='answers',
    )
    answer_text = models.TextField(blank=True, null=True)
    is_correct = models.BooleanField(default=False, db_default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['submission', 'question'],
                name='content_answer_submission_question_uq',
            ),
        ]

    def __str__(self):
        return f'{self.submission} - Q{self.question_id}'
