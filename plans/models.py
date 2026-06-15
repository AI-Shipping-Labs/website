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

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from content.access import LEVEL_MAIN
from content.access import (
    VISIBILITY_CHOICES as TIER_VISIBILITY_CHOICES,
)
from content.models.mixins import TimestampedModelMixin

SPRINT_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('completed', 'Completed'),
]

VISIBILITY_CHOICES = [
    ('internal', 'Internal (staff only)'),
    ('external', 'External (shareable with member)'),
]

# Plan-level visibility (issue #440). ``private`` is the safe default; only
# the owner and staff see private plans. ``cohort`` opens the plan to other
# members of the same sprint via the cohort board. ``public`` is RESERVED
# for a future issue and is deliberately NOT included in the active choices
# tuple -- a separate later migration will add it.
PLAN_VISIBILITY_CHOICES = [
    ('private', 'Private (only the member and staff)'),
    ('cohort', 'Cohort (visible to other members of the same sprint)'),
    # 'public' is reserved for a future issue. Do NOT add it here.
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
    # Minimum tier level required for a member to self-join (issue #443).
    # Default is Main (20) so community members can join by default; staff
    # can lower per-sprint (e.g. 0 for an open pilot) or raise to Premium
    # for high-touch sprints. The choices come from
    # ``content.access.VISIBILITY_CHOICES`` so the same level integers used
    # elsewhere for content gating apply here too.
    min_tier_level = models.IntegerField(
        default=LEVEL_MAIN,
        choices=TIER_VISIBILITY_CHOICES,
        help_text=(
            'Minimum tier level required to join this sprint. Default 20 '
            '(Main); staff can lower per-sprint, e.g. 0 for an open '
            'pilot, or raise to Premium for high-touch sprints.'
        ),
    )
    # Optional link to a recurring meeting series (issue #565).
    # ``SET_NULL`` (not ``CASCADE``) because deleting the event series must
    # only sever the link; the sprint itself and every other sprint that
    # referenced the series survive. ForeignKey (not OneToOne) because the
    # same recurring meeting series can back several sprints at once
    # (e.g. "Wednesday office hours" running across May and June cohorts).
    event_series = models.ForeignKey(
        'events.EventSeries',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='sprints',
        help_text=(
            'Optional recurring meeting series whose occurrences are '
            'surfaced on the sprint detail page. Deleting the series '
            'unlinks the sprint; the sprint itself is preserved.'
        ),
    )

    class Meta:
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return self.name

    @property
    def end_date(self):
        """Derived (non-stored) end of the sprint window.

        ``start_date + duration_weeks`` weeks, using plain ``date``
        arithmetic so it stays timezone-safe (sprint dates are calendar
        ``DateField`` values, never datetimes). This is the exclusive end /
        hand-off date: the day the next back-to-back sprint starts. There is
        no stored ``end_date`` column -- ``start_date`` + ``duration_weeks``
        remain the persisted source of truth and this always stays
        consistent with them. Returns ``None`` if either input is missing.
        """
        if self.start_date is None or self.duration_weeks is None:
            return None
        return self.start_date + timedelta(weeks=self.duration_weeks)

    def get_studio_edit_url(self):
        return f'/studio/sprints/{self.pk}/edit'


class SprintEnrollment(TimestampedModelMixin, models.Model):
    """Authoritative membership row for a sprint (issue #443).

    A user is "in" a sprint iff a ``SprintEnrollment`` row exists for the
    pair. Plans (``plans.Plan``) used to imply membership; that proxy is
    replaced by this table. A ``post_save`` signal on ``Plan`` ensures
    plan creation back-creates the enrollment so legacy code paths
    (Studio plan create, the API plans bulk-import, the cohort board
    tests in #440) keep working unchanged.

    ``enrolled_by`` is ``NULL`` when the member self-joined and points at
    the staff user otherwise. We use ``SET_NULL`` (not the default
    ``CASCADE``) because deleting a staff account must NOT delete every
    enrollment they ever created -- enrollment history is audit data and
    survives the staff user.
    """

    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name='enrollments',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sprint_enrollments',
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text=(
            'Staff user who enrolled this member, or NULL when the '
            'member self-joined.'
        ),
    )

    class Meta:
        ordering = ['-enrolled_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sprint', 'user'],
                name='unique_sprint_enrollment',
            ),
        ]
        indexes = [
            models.Index(fields=['sprint']),
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f'{self.user} in {self.sprint}'


class SprintFeedbackRequest(TimestampedModelMixin, models.Model):
    """Associates a feedback questionnaire with a sprint (issue #803).

    The sprint-specific knowledge of "this questionnaire collects this
    sprint's end-of-sprint feedback" lives ONLY here. The
    ``questionnaires`` app stays generic and never imports ``plans``; the
    one-directional FK from ``plans`` to ``questionnaires`` keeps the
    dependency acyclic.

    ``questionnaire`` is ``PROTECT`` so a questionnaire that already
    collected sprint feedback cannot be silently hard-deleted out from
    under its responses. ``sprint`` is ``CASCADE`` -- deleting the sprint
    removes the linkage (the responses themselves hang off the
    questionnaire, not the sprint).

    A sprint MAY hold more than one feedback request over time (e.g. a
    mid-sprint pulse plus an end-of-sprint survey), so ``sprint`` is NOT
    unique on its own; only ``(sprint, questionnaire)`` is unique, so the
    same questionnaire is attached at most once.

    ``distributed_at`` is stamped the first time responses are
    distributed and stays set afterward (re-running distribution to pick
    up late enrollees does not change it). ``created_by`` is audit-only
    and survives staff deletion via ``SET_NULL``.
    """

    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name='feedback_requests',
    )
    questionnaire = models.ForeignKey(
        'questionnaires.Questionnaire',
        on_delete=models.PROTECT,
        related_name='sprint_feedback_requests',
    )
    distributed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sprint', 'questionnaire'],
                name='unique_sprint_feedback_questionnaire',
            ),
        ]

    def __str__(self):
        return f'{self.questionnaire} for {self.sprint}'


class SprintFeedbackSummary(TimestampedModelMixin, models.Model):
    """AI synthesis of a sprint's collected feedback (issue #805).

    Stores the validated structured output of
    :func:`integrations.services.feedback_synthesis.synthesize_feedback`
    so staff re-open the sprint detail page without re-paying for an LLM
    call, and so #809's eval harness can compare stored runs against a
    real provider. There is at most one current summary per feedback
    request; regenerating overwrites the single row via
    ``update_or_create`` keyed on ``feedback_request``.

    ``response_count`` records how many submitted responses fed the
    synthesis, so the UI can flag a stored summary as stale once more
    members submit. ``model_name`` records the resolved LLM model for
    provenance. ``generated_by`` is ``SET_NULL`` because the audit row
    survives deletion of the staff account that triggered it.
    """

    feedback_request = models.OneToOneField(
        SprintFeedbackRequest,
        on_delete=models.CASCADE,
        related_name='summary',
    )
    result_json = models.JSONField(
        help_text=(
            'The validated FeedbackSynthesisResult as a dict (themes, '
            'what_went_well, what_to_improve, recommendations, '
            'next_sprint_signal, response_count).'
        ),
    )
    response_count = models.IntegerField(
        help_text='Number of submitted responses synthesized.',
    )
    model_name = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='Resolved LLM model used, for provenance.',
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    generated_at = models.DateTimeField()

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f'Feedback summary for {self.feedback_request}'


class PlanQuerySet(models.QuerySet):
    """Visibility-aware queryset for :class:`Plan` (issue #440).

    The cohort board and the read-only individual plan view MUST query
    plans through these helpers, never through a raw
    ``Plan.objects.filter(visibility='cohort')`` -- the gating logic
    (viewer-is-enrolled, viewer-is-not-the-owner, distinct-sprint
    isolation) lives here so views stay thin and a regression test can
    forbid visibility literals in view bodies.
    """

    def visible_on_cohort_board(self, *, sprint, viewer):
        """Plans that should appear on ``sprint``'s cohort board for ``viewer``.

        Returns:
        - Empty queryset if viewer is anonymous / unauthenticated.
        - Empty queryset if viewer is NOT enrolled in ``sprint`` (no
          ``SprintEnrollment`` row for the pair), even if viewer is_staff
          -- the board is the member view; staff use Studio for full
          access. Membership is authoritative via ``SprintEnrollment``
          (issue #443); plan-existence is no longer the proxy.
        - Otherwise: plans in ``sprint`` with cohort visibility whose
          owner is also enrolled in this sprint, excluding the viewer's
          own plan. Filtering by enrollment on BOTH sides means a plan
          whose owner had their enrollment removed (e.g. via
          ``DELETE /api/sprints/<slug>/enrollments/<email>``) drops off
          the board even if the visibility was not auto-privated.
        """
        if viewer is None or not getattr(viewer, 'is_authenticated', False):
            return self.none()
        viewer_enrolled = SprintEnrollment.objects.filter(
            sprint=sprint, user=viewer,
        ).exists()
        if not viewer_enrolled:
            return self.none()
        return self.filter(
            sprint=sprint,
            visibility='cohort',
            member__sprint_enrollments__sprint=sprint,
        ).exclude(member=viewer).distinct()

    def cohort_progress_rows(self, *, sprint, viewer):
        """Plans for ``sprint``'s cohort progress board, regardless of visibility.

        Sibling helper to :meth:`visible_on_cohort_board` (issue #461).
        The progress board renders one row per enrolled member, with
        cohort-visibility plans clickable and private-visibility plans
        rendered as counts-only stubs. This queryset returns the plans
        that back those rows -- visibility filtering is intentionally
        NOT applied here; the view layer classifies each row by
        ``visibility`` to decide whether to expose plan content.

        Returns:
        - Empty queryset if viewer is anonymous / unauthenticated.
        - Empty queryset if viewer is NOT enrolled in ``sprint`` (no
          ``SprintEnrollment`` row for the pair). Mirrors
          :meth:`visible_on_cohort_board` -- the board is member-scoped.
        - Otherwise: plans in ``sprint`` whose owner is also enrolled in
          this sprint, including the viewer's own plan (the view
          renders the viewer row inline). Annotated with
          ``progress_total`` and ``progress_done`` checkpoint counts via
          the same ``Count`` pattern used elsewhere on the board.
        """
        if viewer is None or not getattr(viewer, 'is_authenticated', False):
            return self.none()
        viewer_enrolled = SprintEnrollment.objects.filter(
            sprint=sprint, user=viewer,
        ).exists()
        if not viewer_enrolled:
            return self.none()
        return self.filter(
            sprint=sprint,
            member__sprint_enrollments__sprint=sprint,
        ).annotate(
            progress_total=models.Count('weeks__checkpoints', distinct=True),
            progress_done=models.Count(
                'weeks__checkpoints',
                filter=models.Q(weeks__checkpoints__done_at__isnull=False),
                distinct=True,
            ),
        ).distinct()

    def visible_to_member(self, *, plan_id, viewer):
        """Single plan visible to ``viewer`` for the read-only individual view.

        Returns a queryset (0 or 1 row) so the caller can use
        ``.get()`` / ``get_object_or_404``. Visibility rules:
        - Owner can always see their own plan (regardless of visibility).
        - Other sprint members (i.e. users with a ``SprintEnrollment``
          for the same sprint) can see a cohort-visibility plan.
        - Anonymous / non-enrolled / not-cohort -> empty.
        """
        if viewer is None or not getattr(viewer, 'is_authenticated', False):
            return self.none()
        base = self.filter(pk=plan_id)
        owner_q = models.Q(member=viewer)
        cohort_q = models.Q(visibility='cohort') & models.Q(
            sprint__enrollments__user=viewer,
        )
        return base.filter(owner_q | cohort_q).distinct()


class Plan(TimestampedModelMixin, models.Model):
    """One plan per member per sprint.

    Stores the shareable Summary + Plan blocks; weekly content is in the
    ``Week`` child rows. ``shared_at`` is the explicit timestamp the
    staff stamp when they share the plan with the member; absent until
    that moment.

    ``visibility`` (issue #440) controls who can see the plan:
    ``private`` (the default) is owner + staff only; ``cohort`` opens it
    up to other members of the same sprint via the cohort board. The
    enum reserves ``public`` for a future issue but does NOT include it
    in the active choices -- adding it requires a separate migration.
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
    visibility = models.CharField(
        max_length=10,
        choices=PLAN_VISIBILITY_CHOICES,
        default='private',
    )

    # Short sprint headline. Visibility follows the plan; longer private
    # context stays in the summary fields below.
    goal = models.CharField(max_length=280, blank=True, default='')

    # Owner-only Details block (matches the old bullets in ``_plan.md``)
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

    # When the plan was actually sent to the member.
    shared_at = models.DateTimeField(null=True, blank=True)

    # Stable UUID bridge to the existing ``comments`` app (issue #499).
    # Plan comments are stored with ``Comment.content_id =
    # plan.comment_content_id``; this is the ONLY bridge from plans to
    # comments. Do NOT add ``PlanComment`` / ``PlanCommentReply`` /
    # plan-specific vote tables -- the comments app already covers all
    # of that surface. ``editable=False`` keeps the field out of
    # ModelForms by default; the value is generated on insert and
    # never changes.
    comment_content_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
    )

    objects = PlanQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['member', 'sprint'],
                name='unique_plan_per_member_per_sprint',
            ),
        ]
        indexes = [
            models.Index(fields=['sprint', 'visibility']),
        ]

    def __str__(self):
        return f'{self.member} — {self.sprint}'

    def mark_shared(self):
        """Stamp ``shared_at = timezone.now()`` and persist.

        Issue #732: callers (Studio share button + API PATCH share path)
        capture ``was_already_shared = self.shared_at is not None`` BEFORE
        calling this method, then decide whether the operator's intent is
        a first-time share or an explicit re-share. The model is
        deliberately neutral on that distinction — both flows just want
        the timestamp moved to ``now()``.

        Returns the (saved) plan instance for chaining.
        """
        self.shared_at = timezone.now()
        # ``updated_at`` is ``auto_now=True`` but ``save(update_fields=...)``
        # only refreshes columns named in the list; include it explicitly
        # so the API contract stays consistent with the full-save path.
        self.save(update_fields=['shared_at', 'updated_at'])
        return self


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
    """An entry in the Next Steps block."""

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name='next_steps',
    )
    description = models.TextField()
    position = models.PositiveSmallIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['plan', 'position', 'id']

    def __str__(self):
        return self.description[:80]


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


class PlanRequest(TimestampedModelMixin, models.Model):
    """Audit row for a "ping the team to plan with me" request (issue #585).

    Recorded each time an enrolled sprint member without a plan asks
    the team to prepare one. Multiple rows are kept on purpose so we
    have a full audit history; the rate limit (one ping per 24 hours
    per ``(sprint, member)`` pair) is enforced in the view layer via
    ``PlanRequest.objects.filter(sprint=..., member=...,
    created_at__gte=now-24h).exists()`` rather than via a unique
    constraint.

    ``on_delete=CASCADE`` on both FKs is intentional: this is audit
    data scoped to the (sprint, member) pair; if either side is hard
    deleted the audit row is no longer reachable and can go too.
    """

    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name='plan_requests',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='plan_requests',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sprint', 'member', 'created_at']),
        ]

    def __str__(self):
        return f'PlanRequest({self.member} in {self.sprint})'


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


class NextSprintPlanDraft(TimestampedModelMixin, models.Model):
    """Staff-only AI draft for a plan's NEXT sprint (issue #891, Phase 3).

    Holds the validated structured output of
    :func:`plans.services.next_sprint_draft.draft_next_sprint` ASIDE from
    the plan's live fields — it is advisory text staff review and copy in
    by hand. Phase 3 never auto-writes a draft into a plan. At most one
    current draft per destination plan (``OneToOne``); regenerating
    overwrites the single row via ``update_or_create`` keyed on ``plan``.

    ``source_plan`` records the carry-over / recent-updates source for
    audit and uses ``SET_NULL`` so the draft survives source deletion.
    ``update_count`` is how many recent ``#plan-sprints`` messages informed
    the draft (provenance). ``model_name`` records the resolved LLM model,
    mirroring :class:`SprintFeedbackSummary`. ``generated_by`` is
    ``SET_NULL`` because the audit row survives deletion of the staff
    account that triggered it.

    This data is STAFF-ONLY — no member-facing template renders it.
    """

    plan = models.OneToOneField(
        'plans.Plan',
        on_delete=models.CASCADE,
        related_name='next_sprint_draft',
    )
    result_json = models.JSONField(
        default=dict,
        help_text=(
            'The validated NextSprintDraftResult as a dict '
            '(summary_* fields, goal, suggested_next_steps, rationale).'
        ),
    )
    source_plan = models.ForeignKey(
        'plans.Plan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='The carry-over / recent-updates source plan, for audit.',
    )
    update_count = models.IntegerField(
        default=0,
        help_text=(
            'How many recent #plan-sprints messages informed the draft.'
        ),
    )
    model_name = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='Resolved LLM model used, for provenance.',
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    generated_at = models.DateTimeField()

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f'Next-sprint draft for {self.plan}'
