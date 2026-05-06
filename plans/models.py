"""Database models for personal sprint plans.

See the app docstring in ``plans/__init__.py`` for the relationship to the
legacy markdown plan template (``_plan.md``) and the rationale for storing
plan rows in the database rather than syncing them from a content repo.

The shareable section list (Summary, Plan, Focus, Timeline, Resources,
Deliverables, Accountability, Next Steps) and the internal-only section
list (Persona, Background, Intake, Meeting Notes, Internal
Recommendations, Internal Action Items, Sources) come straight from
``_plan.md``. Internal interview notes live in :class:`InterviewNote` and
MUST be queried via :meth:`InterviewNoteQuerySet.visible_to` so a future
view that forgets to filter them cannot leak staff-only context.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from content.models.mixins import TimestampedModelMixin

SPRINT_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('completed', 'Completed'),
]

PLAN_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('shared', 'Shared'),
    ('active', 'Active'),
    ('completed', 'Completed'),
    ('archived', 'Archived'),
]

VISIBILITY_CHOICES = [
    ('internal', 'Internal (staff only)'),
    ('external', 'External (shareable with member)'),
]

KIND_CHOICES = [
    ('persona', 'Persona'),
    ('background', 'Background'),
    ('intake', 'Intake'),
    ('meeting', 'Meeting Notes'),
    ('recommendation', 'Internal Recommendation'),
    ('action_item', 'Internal Action Item'),
    ('source', 'Source'),
    ('general', 'General'),
]


class Sprint(TimestampedModelMixin, models.Model):
    """A rolling cohort window. A plan belongs to a sprint.

    ``duration_weeks`` is variable per sprint -- the system supports any
    value between 1 and 26 (validated). The default is 6 because that is
    the most common sprint length, but no other code path may hardcode 6.
    Plans tied to 4-week or 8-week sprints must render and edit cleanly.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    start_date = models.DateField()
    duration_weeks = models.PositiveSmallIntegerField(
        default=6,
        validators=[MinValueValidator(1), MaxValueValidator(26)],
    )
    status = models.CharField(
        max_length=20,
        choices=SPRINT_STATUS_CHOICES,
        default='draft',
    )

    class Meta:
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return self.name


class Plan(TimestampedModelMixin, models.Model):
    """One plan per member per sprint.

    Stores the shareable Summary + Plan blocks; weekly content is in the
    ``Week`` child rows. ``shared_at`` is a real timestamp distinct from
    ``status='shared'`` so the share moment survives status churn.
    """

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='plans',
    )
    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.PROTECT,
        related_name='plans',
    )
    status = models.CharField(
        max_length=20,
        choices=PLAN_STATUS_CHOICES,
        default='draft',
    )

    # Shareable Summary block (matches the bullets in ``_plan.md``)
    summary_current_situation = models.TextField(blank=True, default='')
    summary_goal = models.TextField(blank=True, default='')
    summary_main_gap = models.TextField(blank=True, default='')
    summary_weekly_hours = models.CharField(
        max_length=120, blank=True, default='',
    )
    summary_why_this_plan = models.TextField(blank=True, default='')

    # Focus / Accountability blocks
    focus_main = models.TextField(blank=True, default='')
    focus_supporting = models.JSONField(default=list, blank=True)
    accountability = models.TextField(blank=True, default='')

    # Free-text persona label (e.g. "Sam — The Technical Professional
    # Moving to AI"). Not modeled as an enum yet; if we later need a
    # canonical persona table, that's a separate migration.
    assigned_persona = models.CharField(
        max_length=120, blank=True, default='',
    )

    # When the plan was actually sent to the member. Distinct from the
    # ``shared`` status value so we keep a real timestamp.
    shared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['member', 'sprint'],
                name='unique_plan_per_member_per_sprint',
            ),
        ]
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['sprint', 'status']),
        ]

    def __str__(self):
        return f'{self.member} — {self.sprint}'


class Week(TimestampedModelMixin, models.Model):
    """A weekly block within a plan.

    ``week_number`` is unique per plan. The number of weeks per plan is
    typically bounded by ``plan.sprint.duration_weeks``, but that bound
    is NOT enforced at the DB layer -- staff add or remove weeks as the
    plan evolves. ``position`` is future-proofing for cross-week reorder
    in #434; default sort uses ``position`` then ``week_number``.
    """

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name='weeks',
    )
    week_number = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
    )
    theme = models.CharField(max_length=200, blank=True, default='')
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['plan', 'position', 'week_number']
        constraints = [
            models.UniqueConstraint(
                fields=['plan', 'week_number'],
                name='unique_week_number_per_plan',
            ),
        ]

    def __str__(self):
        return f'Week {self.week_number} — {self.plan_id}'


class Checkpoint(TimestampedModelMixin, models.Model):
    """A single bullet inside a week.

    Cross-week reorder (drag a checkpoint from week 2 into week 3) is
    supported by changing both ``week`` and ``position`` -- there is no
    redundant denormalised week index.
    """

    week = models.ForeignKey(
        Week, on_delete=models.CASCADE, related_name='checkpoints',
    )
    description = models.TextField()
    position = models.PositiveSmallIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['week', 'position', 'id']
        indexes = [
            models.Index(fields=['week', 'position']),
        ]

    def __str__(self):
        return self.description[:80]


class Resource(TimestampedModelMixin, models.Model):
    """A link in the Resources block on a plan.

    Plan-level (not week-level) because real plans list resources
    globally. ``url`` is optional -- some Resources entries in real
    plans are unlinked (e.g. "Carlos's own project notes").
    """

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name='resources',
    )
    title = models.CharField(max_length=300)
    url = models.URLField(max_length=600, blank=True, default='')
    note = models.TextField(blank=True, default='')
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['plan', 'position', 'id']

    def __str__(self):
        return self.title


class Deliverable(TimestampedModelMixin, models.Model):
    """An entry in the Deliverables block on a plan."""

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name='deliverables',
    )
    description = models.TextField()
    position = models.PositiveSmallIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['plan', 'position', 'id']

    def __str__(self):
        return self.description[:80]


class NextStep(TimestampedModelMixin, models.Model):
    """An entry in the Next Steps block.

    ``assignee_label`` is free text. Real plans use varied names
    ("Carlos", "Alexey", "Valeriia", "Member"), so we keep it loose
    rather than wiring it to ``User``.
    """

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name='next_steps',
    )
    assignee_label = models.CharField(max_length=120)
    description = models.TextField()
    position = models.PositiveSmallIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['plan', 'position', 'id']

    def __str__(self):
        return f'{self.assignee_label}: {self.description[:60]}'


class WeekNote(TimestampedModelMixin, models.Model):
    """Optional member-authored "how the week went" comment.

    Unsure if anyone will use this; ship it now but do not surface it
    prominently in the Studio UI yet (it does not have its own admin
    page in #432).
    """

    week = models.ForeignKey(
        Week, on_delete=models.CASCADE, related_name='notes',
    )
    body = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        ordering = ['week', '-created_at']

    def __str__(self):
        return f'Note on week {self.week_id}'


class InterviewNoteQuerySet(models.QuerySet):
    """Visibility-aware queryset for :class:`InterviewNote`.

    Internal-vs-external enforcement happens here, not just in templates.
    A future API (#433) or new view that forgets a template-side filter
    must NOT be able to leak ``internal`` notes -- callers MUST go
    through :meth:`visible_to`.
    """

    def external(self):
        """Only notes shareable with the member."""
        return self.filter(visibility='external')

    def internal(self):
        """Staff-only notes."""
        return self.filter(visibility='internal')

    def visible_to(self, user):
        """All notes visible to the given user.

        Staff see everything. Non-staff authenticated users see only
        ``external`` notes for plans where they are the member.
        Anonymous / ``None`` users see nothing.
        """
        if user is None or not getattr(user, 'is_authenticated', False):
            return self.none()
        if user.is_staff:
            return self.all()
        return self.filter(member=user, visibility='external')


class InterviewNote(TimestampedModelMixin, models.Model):
    """Internal interview / intake notes.

    The most security-sensitive table in this app. Internal notes must
    NEVER leak to the member. Use ``InterviewNote.objects.visible_to(
    request.user)`` rather than ``InterviewNote.objects.filter(
    member=request.user)`` in member-facing code.
    """

    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='interview_notes',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='interview_notes',
    )
    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default='internal',
    )
    kind = models.CharField(
        max_length=20,
        choices=KIND_CHOICES,
        default='general',
    )
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='authored_interview_notes',
    )

    objects = InterviewNoteQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['member', 'visibility']),
            models.Index(fields=['plan', 'visibility']),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} note for {self.member}'
