"""Database models for the flexible questionnaire system (issue #800).

See the app docstring in ``questionnaires/__init__.py`` for the
two-layer design (authored template vs per-respondent instance) and the
relationship to dependent issues #801-#805.

Module-level choice constants (``PURPOSE_CHOICES``,
``QUESTION_TYPE_CHOICES``, ``RESPONSE_STATUS_CHOICES``) are the stable
import surface for dependent apps. Import them from here rather than
re-declaring so #801 (onboarding) / #803 (feedback) filter on the same
literals.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from content.models.mixins import TimestampedModelMixin

# ``purpose`` distinguishes onboarding intake from sprint feedback. #801
# and #803 filter questionnaires on this field; keep the literals stable.
PURPOSE_CHOICES = [
    ('onboarding', 'Onboarding'),
    ('feedback', 'Feedback'),
    ('general', 'General'),
]

# The answer types observed in the real member docs: mostly free text and
# long text, plus single/multiple choice and a numeric/scale answer.
QUESTION_TYPE_CHOICES = [
    ('text', 'Short text'),
    ('long_text', 'Long text'),
    ('single_choice', 'Single choice'),
    ('multiple_choice', 'Multiple choice'),
    ('scale', 'Scale / rating'),
    ('number', 'Number'),
]

# A response is a working draft until the respondent submits it.
RESPONSE_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
]

# Choice-type question types share the same answer storage
# (``selected_options`` rows). Used by Studio + answer validation.
_CHOICE_TYPES = frozenset({'single_choice', 'multiple_choice'})


class Questionnaire(TimestampedModelMixin, models.Model):
    """The authored template: metadata plus an ordered base question set.

    The base question set (``questions``) is the default every respondent
    starts from. Per-respondent overrides live on ``ResponseQuestion``,
    not here -- editing the base set never mutates an in-flight response.
    """

    title = models.CharField(max_length=300)
    slug = models.SlugField(unique=True)
    purpose = models.CharField(
        max_length=20,
        choices=PURPOSE_CHOICES,
        default='general',
    )
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # Derive the slug from the title when blank, mirroring the sprint
        # create/edit slug handling.
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    @property
    def question_count(self):
        """Number of base questions in this questionnaire."""
        return self.questions.count()

    @property
    def response_count(self):
        """Number of responses collected for this questionnaire."""
        return self.responses.count()


class Question(TimestampedModelMixin, models.Model):
    """A single question in a questionnaire's base set.

    The base set is the template. Per-respondent overrides are snapshotted
    onto ``ResponseQuestion`` rows; this row is never mutated per
    respondent.
    """

    questionnaire = models.ForeignKey(
        Questionnaire,
        on_delete=models.CASCADE,
        related_name='questions',
    )
    question_type = models.CharField(
        max_length=20,
        choices=QUESTION_TYPE_CHOICES,
    )
    prompt = models.TextField()
    help_text = models.TextField(blank=True, default='')
    is_required = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    # Only meaningful for ``scale`` / ``number`` types.
    scale_min = models.IntegerField(null=True, blank=True)
    scale_max = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.prompt[:80]

    @property
    def is_choice_type(self):
        """True for ``single_choice`` / ``multiple_choice`` questions."""
        return self.question_type in _CHOICE_TYPES


class QuestionOption(TimestampedModelMixin, models.Model):
    """A choice option for a single/multiple-choice base question."""

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='options',
    )
    label = models.CharField(max_length=300)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.label


class Response(TimestampedModelMixin, models.Model):
    """One respondent's submission to a questionnaire.

    At most one response per ``(questionnaire, respondent)`` pair (a DB
    constraint). #802 collects one onboarding response per member; #803
    one feedback response per enrolled member.
    """

    questionnaire = models.ForeignKey(
        Questionnaire,
        on_delete=models.CASCADE,
        related_name='responses',
    )
    respondent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='questionnaire_responses',
    )
    status = models.CharField(
        max_length=20,
        choices=RESPONSE_STATUS_CHOICES,
        default='draft',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['questionnaire', 'respondent'],
                name='unique_response_per_respondent_per_questionnaire',
            ),
        ]

    def __str__(self):
        return f'{self.respondent} -> {self.questionnaire}'

    def mark_submitted(self):
        """Flip status to ``submitted`` and stamp ``submitted_at``."""
        self.status = 'submitted'
        self.submitted_at = timezone.now()
        self.save(update_fields=['status', 'submitted_at', 'updated_at'])
        return self


class ResponseQuestion(TimestampedModelMixin, models.Model):
    """A per-respondent question within a response (issue #802 seam).

    Base ``Question`` rows are the template; ``ResponseQuestion`` rows are
    the per-respondent instance. A response materializes its own ordered
    list of questions (snapshotted from the base set), so a respondent's
    effective question set can differ from the questionnaire's base set
    without mutating the shared ``Question`` rows.

    Snapshotting (not live FK reads of the base question) means editing a
    base question later never silently rewrites questions a member has
    already started answering.

    ``source_question`` links back to the base question this was copied
    from. It is null when staff added a one-off question just for this
    respondent. Choice options are snapshotted as ``ResponseQuestionOption``
    rows so a customized question is fully self-contained.
    """

    response = models.ForeignKey(
        Response,
        on_delete=models.CASCADE,
        related_name='response_questions',
    )
    source_question = models.ForeignKey(
        Question,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='response_questions',
    )
    question_type = models.CharField(
        max_length=20,
        choices=QUESTION_TYPE_CHOICES,
    )
    prompt = models.TextField()
    help_text = models.TextField(blank=True, default='')
    is_required = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    scale_min = models.IntegerField(null=True, blank=True)
    scale_max = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.prompt[:80]

    @property
    def is_choice_type(self):
        """True for ``single_choice`` / ``multiple_choice`` questions."""
        return self.question_type in _CHOICE_TYPES

    @property
    def is_custom(self):
        """True when this is a per-respondent one-off (no base source)."""
        return self.source_question_id is None


class ResponseQuestionOption(TimestampedModelMixin, models.Model):
    """Snapshotted choice option for a choice-type ``ResponseQuestion``."""

    response_question = models.ForeignKey(
        ResponseQuestion,
        on_delete=models.CASCADE,
        related_name='options',
    )
    source_option = models.ForeignKey(
        QuestionOption,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    label = models.CharField(max_length=300)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.label


class Answer(TimestampedModelMixin, models.Model):
    """One answer to one ``ResponseQuestion`` within a response.

    Storage by type: ``text``/``long_text`` -> ``text_value``;
    ``scale``/``number`` -> ``number_value``;
    ``single_choice``/``multiple_choice`` -> ``selected_options``.
    """

    response = models.ForeignKey(
        Response,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    question = models.ForeignKey(
        ResponseQuestion,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    text_value = models.TextField(blank=True, default='')
    number_value = models.IntegerField(null=True, blank=True)
    selected_options = models.ManyToManyField(
        ResponseQuestionOption,
        blank=True,
        related_name='answers',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['response', 'question'],
                name='unique_answer_per_question_per_response',
            ),
        ]

    def __str__(self):
        return f'Answer to {self.question_id} in response {self.response_id}'

    @property
    def display_value(self):
        """Human-readable answer regardless of type.

        Used by the Studio response-viewing surface. #805 reads answers
        via the ORM, not this property. Returns an empty string when the
        answer is blank so callers can render an explicit blank marker.
        """
        qtype = self.question.question_type
        if qtype in _CHOICE_TYPES:
            labels = [opt.label for opt in self.selected_options.all()]
            return ', '.join(labels)
        if qtype in ('scale', 'number'):
            if self.number_value is None:
                return ''
            return str(self.number_value)
        return self.text_value or ''
