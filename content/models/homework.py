"""Homework submission models — issue #1683, tranche 1.

Field names, choice values, and constraints mirror
``community_base.coursework`` (`DataTalksClub/community-base`) exactly,
except where documented on the issue, so a later package adoption is a
mapping rather than a rewrite. Worth remembering when reading this file:

- ``Question.correct_answer`` is plaintext, not the donor's encrypted
  envelope (the content repo is already private; the actual leak surface is
  rendering, not storage -- enforced by never putting ``correct_answer`` /
  ``Answer.is_correct`` in a student-facing context, not by crypto).

Cmp parity (issue #1917): ``Homework.is_accepting_submissions`` gates on
``state`` alone, exactly like the donor. A deadline is informational only
(``is_past_due`` feeds the "Was due <date>" display); late submissions
stay accepted until an operator flips ``state`` to ``CLOSED``/``SCORED``
via Django admin. Sync never writes ``state``, so a manually closed form
survives re-syncs.

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
        """True while ``state == OPEN``, matching cmp parity (issue #1917).

        The deadline is informational: a dated cohort may keep submitting
        past ``due_date`` until an operator flips ``state`` to ``CLOSED``
        or ``SCORED`` (``HomeworkAdmin`` exposes the flip; sync never
        writes ``state``, so a manual close survives re-syncs). This
        restores the donor's ``state``-only gate -- the ``due_date``
        clause #1683 tranche 1 added as a stopgap (no operator surface
        back then) is gone. ``is_past_due`` carries the display-only
        "Was due" wording.
        """
        return self.state == HomeworkState.OPEN

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

        Since issue #1917 closed homework says "closed" in every cohort
        mode -- ``is_accepting_submissions`` False can only mean
        ``CLOSED``/``SCORED``, so closed copy never claims a deadline
        passed. This property still drives deadline display (self-paced
        homework shows no deadline line) and the reveal policy (results
        revealed on submit, locked after).
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
        # ``aisl_`` prefix: community_base.coursework.Submission.student owns
        # ``homework_submissions`` on User (#1696 F2).
        related_name='aisl_homework_submissions',
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


class HomeworkStepThread(models.Model):
    """Q&A thread identity for one homework stepper page (issue #1897).

    On a stepper homework, every question step, the ``learning-in-public``
    step (when present), and ``review`` own their own comment thread; the
    ``intro`` step keeps mounting the unit's own ``content_id`` thread. The
    pair (unit's stable content identity, public step slug) maps to one
    deterministic ``content_id`` (``uuid5``, see
    ``content.services.homework_step_threads``), so a content re-sync never
    mints a new UUID, the same curriculum step shares one thread across
    cohorts (``?cohort=`` never forks it), and changing a question's
    ``source_question_id`` in source is simply a new, empty thread.

    ``unit_content_id`` is a plain UUID mirror of ``Unit.source_content_id``
    rather than a foreign key: content sync may rebuild ``Unit`` rows, and
    these threads -- plus the comments on them -- must survive that
    untouched, the same non-cascade rule as ``Unit`` and ``WorkshopPage``.
    Removing a question from source unmounts its page, but its row (and
    comments) stays stored and resolvable for operators.
    """

    unit_content_id = models.UUIDField(
        db_index=True,
        help_text="Stable content UUID of the curriculum unit that owns the homework.",
    )
    step_slug = models.CharField(
        max_length=128,
        help_text=(
            "Public step slug in the canonical step URL: an authored question "
            "source_question_id, 'learning-in-public', or 'review'."
        ),
    )
    content_id = models.UUIDField(
        unique=True,
        help_text=(
            "Deterministic thread UUID for the (unit identity, step slug) pair; "
            "the value the browser comments API mounts for this step page."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['unit_content_id', 'step_slug'],
                name='content_homework_step_thread_unit_slug_uq',
            ),
        ]
        ordering = ['unit_content_id', 'step_slug']

    def __str__(self):
        return f'{self.unit_content_id} step {self.step_slug}'
