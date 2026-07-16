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
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from accounts.utils.display import display_name
from accounts.utils.user_checks import is_authenticated_user, is_staff_user
from content.access import LEVEL_MAIN
from content.access import (
    VISIBILITY_CHOICES as TIER_VISIBILITY_CHOICES,
)
from content.models.mixins import TimestampedModelMixin
from plans.interview_note_utils import (
    normalize_note_body,
    normalize_note_tags,
    normalize_source_metadata,
    normalize_source_type,
)

# Stored operator/admin state. This is intentionally separate from the
# date-derived lifecycle badge returned by ``Sprint.sprint_badge()``.
SPRINT_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
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

NEXT_STEP_KIND_PRE_SPRINT = 'pre_sprint'
NEXT_STEP_KIND_NEXT_STEP = 'next_step'

NEXT_STEP_KIND_CHOICES = [
    (NEXT_STEP_KIND_PRE_SPRINT, 'Pre-sprint action'),
    (NEXT_STEP_KIND_NEXT_STEP, 'Next step'),
]

PLAN_TITLE_MAX_LENGTH = 280

PLAN_READY_EMAIL_STATUS_SENDING = 'sending'
PLAN_READY_EMAIL_STATUS_SENT = 'sent'
PLAN_READY_EMAIL_STATUS_FAILED = 'failed'

PLAN_READY_EMAIL_STATUS_CHOICES = [
    (PLAN_READY_EMAIL_STATUS_SENDING, 'Sending'),
    (PLAN_READY_EMAIL_STATUS_SENT, 'Sent'),
    (PLAN_READY_EMAIL_STATUS_FAILED, 'Failed'),
]

PARTNER_INTRO_EMAIL_STATUS_SENDING = 'sending'
PARTNER_INTRO_EMAIL_STATUS_SENT = 'sent'
PARTNER_INTRO_EMAIL_STATUS_FAILED = 'failed'

PARTNER_INTRO_EMAIL_STATUS_CHOICES = [
    (PARTNER_INTRO_EMAIL_STATUS_SENDING, 'Sending'),
    (PARTNER_INTRO_EMAIL_STATUS_SENT, 'Sent'),
    (PARTNER_INTRO_EMAIL_STATUS_FAILED, 'Failed'),
]

SPRINT_CADENCE_KIND_WEEK_START = 'week_start'
SPRINT_CADENCE_KIND_WEEK_NOTE_PROMPT = 'week_note_prompt'
SPRINT_CADENCE_KIND_SLACK_PROGRESS = 'slack_progress'

SPRINT_CADENCE_KIND_CHOICES = [
    (SPRINT_CADENCE_KIND_WEEK_START, 'Week start'),
    (SPRINT_CADENCE_KIND_WEEK_NOTE_PROMPT, 'Week note prompt'),
    (SPRINT_CADENCE_KIND_SLACK_PROGRESS, 'Slack progress'),
]

SPRINT_CADENCE_STATUS_SENT = 'sent'
SPRINT_CADENCE_STATUS_EMAIL_FAILED = 'email_failed'
SPRINT_CADENCE_STATUS_SKIPPED = 'skipped'

SPRINT_CADENCE_STATUS_CHOICES = [
    (SPRINT_CADENCE_STATUS_SENT, 'Sent'),
    (SPRINT_CADENCE_STATUS_EMAIL_FAILED, 'Email failed'),
    (SPRINT_CADENCE_STATUS_SKIPPED, 'Skipped'),
]

SPRINT_END_DELIVERY_STATUS_SENT = 'sent'
SPRINT_END_DELIVERY_STATUS_EMAIL_FAILED = 'email_failed'
SPRINT_END_DELIVERY_STATUS_SKIPPED = 'skipped'

SPRINT_END_DELIVERY_STATUS_CHOICES = [
    (SPRINT_END_DELIVERY_STATUS_SENT, 'Sent'),
    (SPRINT_END_DELIVERY_STATUS_EMAIL_FAILED, 'Email failed'),
    (SPRINT_END_DELIVERY_STATUS_SKIPPED, 'Skipped'),
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

# Default number of days before start / before end that flip the sprint
# badge to ``Starting soon`` / ``Ending soon``. Overridable at runtime via
# the ``SPRINT_BADGE_WINDOW_DAYS`` IntegrationSetting (issue #979).
SPRINT_BADGE_WINDOW_DAYS_DEFAULT = 7

# Human label + dark-theme pill colour per badge state (issue #979). The
# label and css_class are display-only; the machine ``state`` key is what
# the date logic returns. Pill shape is applied by the template; only the
# colour varies here.
SPRINT_BADGE_DISPLAY = {
    'upcoming': ('Upcoming', 'border border-border text-muted-foreground'),
    'starting_soon': ('Starting soon', 'bg-sky-500/15 text-sky-300'),
    'active': ('Active', 'bg-emerald-500/15 text-emerald-300'),
    'ending_soon': ('Ending soon', 'bg-amber-500/15 text-amber-300'),
    'ended': ('Ended', 'bg-muted text-muted-foreground'),
}


@dataclass(frozen=True)
class SprintBadge:
    """Date-derived sprint lifecycle badge (issue #979).

    Display-only value object returned by :meth:`Sprint.sprint_badge`. It
    never reflects or mutates the stored ``status`` field.
    """

    state: str
    label: str
    css_class: str


class Sprint(TimestampedModelMixin, models.Model):
    """A rolling cohort window. A plan belongs to a sprint.

    ``duration_weeks`` is variable per sprint -- the system supports any
    value between 1 and 26 (validated). The default is 6 because that is
    the most common sprint length, but no other code path may hardcode 6.
    Plans tied to 4-week or 8-week sprints must render and edit cleanly.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True, default='', db_default='')
    outcomes = models.TextField(blank=True, default='', db_default='')
    audience = models.TextField(blank=True, default='', db_default='')
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

    def get_absolute_url(self):
        return reverse('sprint_detail', kwargs={'sprint_slug': self.slug})

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

    def has_ended(self, today=None):
        """True once the sprint window is over."""
        end = self.end_date
        if end is None:
            return False
        if today is None:
            today = timezone.localdate()
        return today >= end

    @staticmethod
    def _badge_window_days():
        """Resolve the badge window ``W`` from config (issue #979).

        Reads ``SPRINT_BADGE_WINDOW_DAYS`` (DB override -> env -> default 7)
        and coerces to a positive int. A blank / non-numeric / non-positive
        override falls back to the default rather than raising.
        """
        from integrations.config import get_config  # noqa: PLC0415

        raw = get_config(
            'SPRINT_BADGE_WINDOW_DAYS',
            str(SPRINT_BADGE_WINDOW_DAYS_DEFAULT),
        )
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return SPRINT_BADGE_WINDOW_DAYS_DEFAULT
        if value <= 0:
            return SPRINT_BADGE_WINDOW_DAYS_DEFAULT
        return value

    def sprint_badge(self, now=None):
        """Return the date-derived lifecycle badge (issue #979).

        Single source of truth for both the ``/sprints`` card and the sprint
        detail page, so neither template nor view duplicates the date logic.
        Display-only: this reads ``start_date`` / ``end_date`` and never
        touches the stored admin ``status`` field (``draft``, ``active``,
        ``completed``, ``cancelled``). Compares plain dates (both bounds are
        calendar dates, not datetimes).

        ``now`` is injectable (defaults to :func:`timezone.localdate`) so
        tests drive each state deterministically without freezing real time.

        Given today ``now``, start ``s``, end ``e`` and window ``W`` days:

        - ``now < s - W``            -> ``upcoming``
        - ``s - W <= now < s``       -> ``starting_soon``
        - ``s <= now <= e - W``      -> ``active``
        - ``e - W < now <= e``       -> ``ending_soon``
        - ``now > e``                -> ``ended``

        Boundary rules: the start day itself is ``active`` (not
        ``starting_soon``); the end day itself is ``ending_soon`` (not
        ``ended``) and ``ended`` begins the day after ``end_date``.

        Overlap rule: for a sprint shorter than ``W`` (where ``e - W < s``
        so the two windows overlap), ``starting_soon`` wins before ``s`` and
        the ``s..e`` range reads ``ending_soon`` -- such a sprint is never
        ``active`` (it goes Starting soon -> Ending soon -> Ended).
        """
        if now is None:
            now = timezone.localdate()

        s = self.start_date
        e = self.end_date
        w = self._badge_window_days()
        window = timedelta(days=w)

        if s is None or e is None:
            state = 'upcoming'
        elif now < s:
            # Before the sprint starts. ``starting_soon`` takes precedence
            # over the end window so a short sprint (e - W < s) still reads
            # ``Starting soon`` in the days before ``s`` rather than jumping
            # straight to ``Ending soon``.
            state = 'starting_soon' if now >= s - window else 'upcoming'
        elif now > e:
            state = 'ended'
        elif now > e - window:
            # On / after start, within W of the end (inclusive of the end
            # day). Checked before ``active`` so the overlap case (short
            # sprint, e - W < s) yields ``ending_soon`` over the whole s..e
            # range instead of ``active``. A sprint shorter than W therefore
            # never reads ``active`` (Starting soon -> Ending soon -> Ended).
            state = 'ending_soon'
        else:
            # s <= now <= e - W. Start day -> active. For a sprint shorter
            # than W this branch is never reached.
            state = 'active'

        label, css_class = SPRINT_BADGE_DISPLAY[state]
        return SprintBadge(state=state, label=label, css_class=css_class)

    @property
    def sprint_badge_current(self):
        """Convenience accessor so templates can render the badge with the
        default (today) ``now``: ``{{ sprint.sprint_badge_current.label }}``.
        """
        return self.sprint_badge()

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


ACCOUNTABILITY_SOURCE_MANUAL = 'manual'
ACCOUNTABILITY_SOURCE_RANDOM = 'random'

ACCOUNTABILITY_SOURCE_CHOICES = [
    (ACCOUNTABILITY_SOURCE_MANUAL, 'Manual'),
    (ACCOUNTABILITY_SOURCE_RANDOM, 'Random'),
]


class SprintAccountabilityPartner(TimestampedModelMixin, models.Model):
    """Directed accountability partner edge for one sprint member.

    Partnering is reciprocal in service/view code: assigning Alice to Bob
    writes Alice -> Bob and Bob -> Alice. Keeping the stored rows directed
    makes the member-facing lookup cheap and allows each member to have one
    or more partners.
    """

    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name='accountability_partners',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sprint_accountability_partners',
    )
    partner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sprint_accountability_partner_for',
    )
    source = models.CharField(
        max_length=20,
        choices=ACCOUNTABILITY_SOURCE_CHOICES,
        default=ACCOUNTABILITY_SOURCE_MANUAL,
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['sprint', 'member', 'partner'],
                name='unique_sprint_accountability_partner',
            ),
            models.CheckConstraint(
                condition=~models.Q(member=models.F('partner')),
                name='sprint_accountability_no_self_partner',
            ),
        ]
        indexes = [
            models.Index(fields=['sprint', 'member']),
            models.Index(fields=['sprint', 'partner']),
        ]

    def __str__(self):
        return f'{self.member} -> {self.partner} in {self.sprint}'


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
        if not is_authenticated_user(viewer):
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
        if not is_authenticated_user(viewer):
            return self.none()
        viewer_enrolled = SprintEnrollment.objects.filter(
            sprint=sprint, user=viewer,
        ).exists()
        if not viewer_enrolled:
            return self.none()
        from plans.services.progress import annotate_plan_progress  # noqa: PLC0415

        return annotate_plan_progress(self.filter(
            sprint=sprint,
            member__sprint_enrollments__sprint=sprint,
        )).distinct()

    def visible_to_member(self, *, plan_id, viewer):
        """Single plan visible to ``viewer`` for the read-only individual view.

        Returns a queryset (0 or 1 row) so the caller can use
        ``.get()`` / ``get_object_or_404``. Visibility rules:
        - Owner can always see their own plan (regardless of visibility).
        - Other sprint members (i.e. users with a ``SprintEnrollment``
          for the same sprint) can see a cohort-visibility plan.
        - Anonymous / non-enrolled / not-cohort -> empty.
        """
        if not is_authenticated_user(viewer):
            return self.none()
        base = self.filter(pk=plan_id)
        owner_q = models.Q(member=viewer)
        cohort_q = models.Q(visibility='cohort') & models.Q(
            sprint__enrollments__user=viewer,
        )
        return base.filter(owner_q | cohort_q).distinct()

    def owner_workspace_for_viewer(self, *, plan_id, sprint_slug, viewer):
        """Plan visible on the owner workspace URL for ``viewer``.

        The owner workspace remains owner-first, but staff operators who
        return from Studio's "View as member" flow need the same URL to
        render after their staff session is restored.
        """
        if not is_authenticated_user(viewer):
            return self.none()
        base = self.filter(pk=plan_id, sprint__slug=sprint_slug)
        if viewer.is_staff:
            return base
        return base.filter(member=viewer)


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
    title = models.CharField(
        max_length=PLAN_TITLE_MAX_LENGTH,
        blank=True,
        default='',
        help_text='Short non-sensitive headline shown on sprint boards.',
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

    def fallback_title(self):
        """Return the deterministic display title for this plan."""
        for candidate in (self.goal, self.summary_goal):
            value = (candidate or '').strip()
            if value:
                return value[:PLAN_TITLE_MAX_LENGTH]

        member_label = display_name(self.member).strip() or 'Member'
        sprint_name = getattr(self.sprint, 'name', '') or 'Sprint'
        generated = f"{member_label}'s {sprint_name} plan"
        return generated[:PLAN_TITLE_MAX_LENGTH]

    @property
    def display_title(self):
        value = (self.title or '').strip()
        if value:
            return value
        return self.fallback_title()

    def ensure_title(self):
        """Persist a trimmed title, deriving one when the field is blank."""
        value = (self.title or '').strip()
        self.title = value[:PLAN_TITLE_MAX_LENGTH] if value else self.fallback_title()
        return self.title

    def save(self, *args, **kwargs):
        before_title = self.title
        self.ensure_title()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            fields = set(update_fields)
            if fields and (
                self.title != before_title
                or any(f in fields for f in ('title', 'goal', 'summary_goal'))
            ):
                fields.add('title')
                kwargs['update_fields'] = list(fields)
        return super().save(*args, **kwargs)

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

    @property
    def pre_sprint_actions(self):
        return self.next_steps.filter(kind=NEXT_STEP_KIND_PRE_SPRINT)

    @property
    def next_step_actions(self):
        return self.next_steps.filter(kind=NEXT_STEP_KIND_NEXT_STEP)


class PlanReadyEmailLog(TimestampedModelMixin, models.Model):
    """Durable per-plan guard for bulk plan-ready email sends.

    The single-plan Share/Re-share action intentionally remains reusable.
    This row only guards the sprint-level bulk ready-email action so browser
    retries, double-clicks, and repeated API calls cannot create duplicate
    successful default sends for the same plan.
    """

    plan = models.OneToOneField(
        Plan,
        on_delete=models.CASCADE,
        related_name='ready_email_log',
    )
    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name='plan_ready_email_logs',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='plan_ready_email_logs',
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    notification = models.ForeignKey(
        'notifications.Notification',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    email_log = models.ForeignKey(
        'email_app.EmailLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    status = models.CharField(
        max_length=16,
        choices=PLAN_READY_EMAIL_STATUS_CHOICES,
        default=PLAN_READY_EMAIL_STATUS_SENDING,
        db_index=True,
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['sprint', 'status']),
            models.Index(fields=['member', 'status']),
        ]

    def __str__(self):
        return f'PlanReadyEmailLog(plan={self.plan_id}, status={self.status})'


class SprintCadenceDeliveryLog(TimestampedModelMixin, models.Model):
    """Idempotency and audit row for sprint cadence notifications."""

    kind = models.CharField(
        max_length=32,
        choices=SPRINT_CADENCE_KIND_CHOICES,
        db_index=True,
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name='sprint_cadence_delivery_logs',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sprint_cadence_delivery_logs',
    )
    week = models.ForeignKey(
        'plans.Week',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='cadence_delivery_logs',
    )
    progress_event = models.ForeignKey(
        'crm.IngestedProgressEvent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='member_delivery_logs',
    )
    source_message_ts = models.CharField(max_length=64, blank=True, default='')
    notification = models.ForeignKey(
        'notifications.Notification',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    email_log = models.ForeignKey(
        'email_app.EmailLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    status = models.CharField(
        max_length=16,
        choices=SPRINT_CADENCE_STATUS_CHOICES,
        default=SPRINT_CADENCE_STATUS_SKIPPED,
        db_index=True,
    )
    last_error = models.TextField(blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'plan', 'week'],
                condition=(
                    models.Q(
                        kind__in=[
                            SPRINT_CADENCE_KIND_WEEK_START,
                            SPRINT_CADENCE_KIND_WEEK_NOTE_PROMPT,
                        ],
                    )
                    & models.Q(week__isnull=False)
                ),
                name='unique_sprint_cadence_plan_week',
            ),
            models.UniqueConstraint(
                fields=['kind', 'progress_event', 'source_message_ts'],
                condition=(
                    models.Q(kind=SPRINT_CADENCE_KIND_SLACK_PROGRESS)
                    & models.Q(progress_event__isnull=False)
                    & ~models.Q(source_message_ts='')
                ),
                name='unique_sprint_cadence_slack_progress',
            ),
        ]
        indexes = [
            models.Index(fields=['kind', 'status']),
            models.Index(fields=['plan', 'kind']),
            models.Index(fields=['member', 'kind']),
        ]

    def __str__(self):
        return (
            'SprintCadenceDeliveryLog('
            f'kind={self.kind}, plan={self.plan_id}, status={self.status})'
        )


class SprintPartnerIntroEmailLog(TimestampedModelMixin, models.Model):
    """Durable per-recipient guard for sprint partner intro emails."""

    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name='partner_intro_email_logs',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sprint_partner_intro_email_logs',
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    email_log = models.ForeignKey(
        'email_app.EmailLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    status = models.CharField(
        max_length=16,
        choices=PARTNER_INTRO_EMAIL_STATUS_CHOICES,
        default=PARTNER_INTRO_EMAIL_STATUS_SENDING,
        db_index=True,
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    partner_snapshot = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sprint', 'member'],
                name='unique_sprint_partner_intro_email_recipient',
            ),
        ]
        indexes = [
            models.Index(fields=['sprint', 'status']),
            models.Index(fields=['member', 'status']),
        ]

    def __str__(self):
        return (
            'SprintPartnerIntroEmailLog('
            f'sprint={self.sprint_id}, member={self.member_id}, '
            f'status={self.status})'
        )


class SprintEndDeliveryLog(TimestampedModelMixin, models.Model):
    """Durable one-shot audit row for sprint-end member recaps."""

    sprint = models.ForeignKey(
        Sprint,
        on_delete=models.CASCADE,
        related_name='sprint_end_delivery_logs',
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sprint_end_delivery_logs',
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sprint_end_delivery_logs',
    )
    notification = models.ForeignKey(
        'notifications.Notification',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    email_log = models.ForeignKey(
        'email_app.EmailLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    feedback_response = models.ForeignKey(
        'questionnaires.Response',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    next_sprint = models.ForeignKey(
        Sprint,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    status = models.CharField(
        max_length=16,
        choices=SPRINT_END_DELIVERY_STATUS_CHOICES,
        default=SPRINT_END_DELIVERY_STATUS_SENT,
        db_index=True,
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sprint', 'member'],
                name='unique_sprint_end_delivery_recipient',
            ),
        ]
        indexes = [
            models.Index(fields=['sprint', 'status']),
            models.Index(fields=['member', 'status']),
        ]

    def __str__(self):
        return (
            'SprintEndDeliveryLog('
            f'sprint={self.sprint_id}, member={self.member_id}, '
            f'status={self.status})'
        )


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
    """A plan-level action item, usually completed before the sprint."""

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name='next_steps',
    )
    kind = models.CharField(
        max_length=20,
        choices=NEXT_STEP_KIND_CHOICES,
        default=NEXT_STEP_KIND_PRE_SPRINT,
    )
    description = models.TextField()
    position = models.PositiveSmallIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['plan', 'position', 'id']

    def __str__(self):
        return self.description[:80]


class WeekNote(TimestampedModelMixin, models.Model):
    """Singleton member-authored "how the week went" note for a week."""

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
        constraints = [
            models.UniqueConstraint(
                fields=['week'],
                name='unique_week_note_per_week',
            ),
        ]

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
        if not is_authenticated_user(user):
            return self.none()
        if is_staff_user(user):
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
    tags = models.JSONField(default=list, blank=True)
    source_type = models.CharField(
        max_length=40,
        blank=True,
        default='',
        db_index=True,
    )
    source_metadata = models.JSONField(default=dict, blank=True)
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

    def save(self, *args, **kwargs):
        self.tags = normalize_note_tags(self.tags)
        self.source_type = normalize_source_type(self.source_type)
        self.source_metadata = normalize_source_metadata(self.source_metadata)
        self.body = normalize_note_body(self.body, self.source_type)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            update_fields.update({'tags', 'source_type', 'source_metadata', 'body'})
            kwargs['update_fields'] = list(update_fields)
        super().save(*args, **kwargs)


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


class FirstSprintPlanDraft(TimestampedModelMixin, models.Model):
    """Staff-only AI draft for a member's first sprint plan.

    Holds structured draft output aside from the live :class:`Plan` fields.
    Staff must explicitly apply the draft, then share the plan separately.
    """

    plan = models.OneToOneField(
        'plans.Plan',
        on_delete=models.CASCADE,
        related_name='first_sprint_draft',
    )
    result_json = models.JSONField(
        default=dict,
        help_text='The validated FirstSprintDraftResult as a dict.',
    )
    source_response = models.ForeignKey(
        'questionnaires.Response',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Submitted onboarding response used as source input.',
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
