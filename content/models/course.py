from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Prefetch

from content.access import (
    UNIT_VISIBILITY_CHOICES,
    VISIBILITY_CHOICES,
    get_required_tier_name,
)
from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)
from content.utils.h1 import strip_leading_title_h1
from content.utils.markdown import render_markdown

STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('published', 'Published'),
]

# Issue #1658: courses sold outside the membership plans (e.g. the Maven
# buildcamp). 'tier' is today's behaviour — subscription tier / TierOverride
# comparison. 'entitlement' skips the tier comparison entirely: only a
# CourseAccess row (purchased or granted) or staff/superuser opens the
# course. See content/access.py for the branch that reads this field.
ACCESS_MODE_CHOICES = [
    ('tier', 'Tier-gated'),
    ('entitlement', 'Entitlement-only'),
]


class Course(
    SyncedContentIdentityMixin,
    SourceMetadataMixin,
    TimestampedModelMixin,
    models.Model,
):
    """Structured course: Course -> Modules -> Units."""

    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(
        blank=True, default='',
        help_text="Markdown description shown on the course detail page.",
    )
    description_html = models.TextField(
        blank=True, default='',
        help_text="Auto-rendered HTML from description markdown.",
    )
    cover_image_url = models.URLField(max_length=500, blank=True, default='')
    auto_banner_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            "Platform-generated OG banner URL (banner-generator Lambda, "
            "issue #788). Overwritten by the auto-banner pipeline; templates "
            "should prefer ``cover_image_url`` and fall back to this."
        ),
    )
    custom_banner_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            "Operator-uploaded custom banner/social image. Survives content "
            "re-sync. Wins over the generated banner; loses to a frontmatter "
            "cover_image_url."
        ),
    )
    auto_banner_title_hash = models.CharField(
        max_length=64, blank=True, default='',
        help_text=(
            "sha256 hex digest of the title used to render the current "
            "``auto_banner_url``. Used to detect title drift between syncs."
        ),
    )
    instructors = models.ManyToManyField(
        'content.Instructor',
        through='content.CourseInstructor',
        related_name='courses',
        blank=True,
        help_text=(
            'Instructors teaching this course. Order is controlled via the '
            'CourseInstructor.position field; the first instructor is the '
            'primary instructor shown on listings and cards.'
        ),
    )
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to access course units.",
    )
    default_unit_required_level = models.IntegerField(
        null=True, blank=True,
        choices=UNIT_VISIBILITY_CHOICES,
        help_text=(
            "Default access level applied to every unit in this course "
            "unless the unit's frontmatter overrides it. When null, units "
            "inherit Course.required_level (legacy behaviour). Issue #465."
        ),
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
    )
    discussion_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text="Slack channel URL for paid courses, GitHub URL for free courses.",
    )
    maven_course_key = models.CharField(
        max_length=255, blank=True, default='', db_default='',
        help_text=(
            "Maven's course identifier string, matched case-insensitively "
            "against the Maven webhook's course_key at enrollment time "
            "(issue #1659). Source-owned from course.yaml; blank means no "
            "Maven course is linked."
        ),
    )
    access_mode = models.CharField(
        max_length=20, choices=ACCESS_MODE_CHOICES, default='tier',
        db_default='tier',
        help_text=(
            "'tier' (default): access follows subscription tier level. "
            "'entitlement': tier comparison is skipped — only a "
            "CourseAccess row or staff/superuser opens the course. For "
            "programs sold outside the membership plans (issue #1658)."
        ),
    )
    enroll_url = models.URLField(
        max_length=500, blank=True, default='', db_default='',
        help_text=(
            "External signup page (e.g. the Maven course page). Only "
            "meaningful when access_mode='entitlement'."
        ),
    )
    program_label = models.CharField(
        max_length=100, blank=True, default='', db_default='',
        help_text=(
            "Short external-program name shown on the 'Sold separately' "
            "badge and enroll CTA, e.g. 'Maven'. Blank falls back to "
            "generic copy."
        ),
    )
    individual_price_eur = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Price for one-time individual purchase in EUR. Null = not sold individually.",
    )
    stripe_product_id = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Stripe product ID for individual purchase.",
    )
    stripe_price_id = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Stripe price ID for individual purchase.",
    )
    tags = models.JSONField(default=list, blank=True)
    testimonials = models.JSONField(
        default=list, blank=True,
        help_text="List of testimonial objects: {quote, name, role?, company?, source_url?}.",
    )
    # Peer review configuration
    peer_review_enabled = models.BooleanField(
        default=False,
        help_text="Toggle peer review on/off for this course.",
    )
    peer_review_count = models.IntegerField(
        default=3,
        help_text="Number of peers each student must review.",
    )
    peer_review_deadline_days = models.IntegerField(
        default=7,
        help_text="Days from batch assignment until review deadline.",
    )
    peer_review_criteria = models.TextField(
        blank=True, default='',
        help_text="Markdown rubric/criteria shown to reviewers.",
    )
    peer_review_criteria_html = models.TextField(
        blank=True, default='',
        help_text="Auto-rendered HTML from peer_review_criteria markdown.",
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/courses/{self.slug}'

    def get_studio_edit_url(self):
        return f'/studio/courses/{self.pk}/edit'

    def save(self, *args, **kwargs):
        from content.utils.linkify import linkify_urls
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)

        if self.description:
            # Strip the leading H1 if it duplicates the course title — the
            # course detail page renders the title as the page heading, so
            # a README that starts with ``# Course Title`` would show up
            # twice (issue #227).
            description_md = strip_leading_title_h1(self.description, self.title)
            self.description_html = linkify_urls(render_markdown(description_md))
        if self.peer_review_criteria:
            self.peer_review_criteria_html = linkify_urls(
                render_markdown(self.peer_review_criteria)
            )
        super().save(*args, **kwargs)

    @property
    def is_published(self):
        return self.status == 'published'

    @property
    def ordered_instructors(self):
        """Return ``Instructor`` rows in ``CourseInstructor.position`` order.

        Templates iterate this directly. Returns an empty list when no
        instructors are attached so callers can ``{% if %}`` cleanly.
        """
        return list(self.instructors.order_by('courseinstructor__position'))

    @property
    def primary_instructor(self):
        """First instructor by position, or ``None`` when unset.

        Used by listings/cards that show a single "by <name>" line.
        """
        return self.instructors.order_by(
            'courseinstructor__position',
        ).first()

    @property
    def is_free(self) -> bool:
        """True when the course has no tier requirement (lead magnet)."""
        return self.required_level == 0

    @property
    def required_tier_name(self):
        return get_required_tier_name(self.required_level)

    @property
    def is_entitlement_mode(self) -> bool:
        """True when tier comparison is skipped for this course (issue #1658).

        Call sites branch on this instead of comparing ``access_mode`` to
        the ``'entitlement'`` magic string directly.
        """
        return self.access_mode == 'entitlement'

    def total_units(self):
        """Return the total number of non-bonus units in this course.

        Issue #1674: ``kind="event"`` units count (no special-casing);
        ``is_bonus`` units/modules are excluded from the denominator. See
        :func:`content.models.course.non_bonus_units` — the one named,
        reusable filter both this and :meth:`completed_units` chain.
        """
        return non_bonus_units(Unit.objects.filter(module__course=self)).count()

    def completed_units(self, user):
        """Return the number of non-bonus units completed by the given user."""
        if user is None or not user.is_authenticated:
            return 0
        return UserCourseProgress.objects.filter(
            user=user,
            unit__in=non_bonus_units(Unit.objects.filter(module__course=self)),
            completed_at__isnull=False,
        ).count()

    def get_syllabus(self):
        """Return top-level modules with their children/units, ordered.

        Issue #1674: modules nest one level deep (``Module.parent``). A
        top-level module either holds units directly (leaf, today's
        two-level shape) or holds child submodules which themselves hold
        units — never both (enforced by :meth:`Module.clean`).

        Prefetches the full tree in four queries total — one per level
        (top-level modules, their children, the children's units, the
        top-level leaves' own units) — regardless of module count,
        preserving the no-N+1 constraint issue #287 established for the
        two-level case: ``module.children.all()`` and ``module.units.all()``
        (on a submodule) read from the prefetch cache rather than issuing
        a fresh ``SELECT`` per module.
        """
        return self.modules.filter(parent__isnull=True).prefetch_related(
            Prefetch(
                'children',
                queryset=Module.objects.order_by('sort_order', 'id').prefetch_related(
                    Prefetch('units', queryset=Unit.objects.order_by('sort_order', 'id')),
                ),
            ),
            Prefetch('units', queryset=Unit.objects.order_by('sort_order', 'id')),
        ).order_by('sort_order', 'id')

    def get_next_unit_for(self, user):
        """Return the next unfinished unit for the given user.

        Walks units in canonical order (module sort_order, then unit
        sort_order) and returns the first unit with no
        ``UserCourseProgress.completed_at`` for this user. Returns
        ``None`` if the user has completed every unit (or the course
        has no units, or the user is anonymous).

        "Next" means the first unfinished unit in reading order, not
        "after the last completed". If the user completed units 1, 3, 5
        but skipped 2 and 4, the next unit is unit 2.

        This walks the course's units in the canonical depth-first reading
        order (:func:`content.services.course_units.get_all_units_ordered`
        — the single ordering helper also used by previous/next
        navigation, so "Continue" always lands on the same unit the
        reader's Next button would). Callers that need to compute this for
        many courses should batch-prefetch the data and resolve next-unit
        in Python — see ``content.views.home._get_in_progress_courses``.
        """
        if user is None or not user.is_authenticated:
            return None
        # Local import: content.services.course_units imports content.models
        # at module load time, so importing it back at module scope here
        # would be circular. Safe at call time — both modules are fully
        # loaded by then.
        from content.services.course_units import get_all_units_ordered
        units = get_all_units_ordered(self)
        if not units:
            return None
        completed_ids = set(
            UserCourseProgress.objects.filter(
                user=user,
                unit__in=units,
                completed_at__isnull=False,
            ).values_list('unit_id', flat=True)
        )
        for unit in units:
            if unit.id not in completed_ids:
                return unit
        return None


def non_bonus_units(queryset):
    """Exclude bonus units/modules from ``queryset`` (issue #1674).

    The single, reusable "what counts toward the progress denominator"
    filter: excludes a unit flagged ``is_bonus``, a unit whose (leaf)
    module is flagged ``is_bonus``, and a unit whose module's *parent* is
    flagged ``is_bonus`` (a whole bonus week/top-level module bonus-flags
    every submodule under it). ``kind="event"`` units are NOT excluded —
    they count toward progress like any other unit (owner decision).
    Every call site that needs "what counts" chains this once rather than
    scattering inline ``is_bonus`` conditionals.
    """
    return queryset.exclude(
        models.Q(is_bonus=True)
        | models.Q(module__is_bonus=True)
        | models.Q(module__parent__is_bonus=True)
    )


class Module(SourceMetadataMixin, models.Model):
    """A module within a course, containing units, OR containing child
    submodules — never both (issue #1674's mixed-content rule)."""

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='modules',
    )
    # Issue #1674: a submodule *is* a Module row with ``parent`` set. Max
    # two levels of module (enforced in ``clean()``): a submodule cannot
    # itself have children.
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='children',
        help_text=(
            "Parent module when this row is a submodule. Null for a "
            "top-level module. Maximum two levels."
        ),
    )
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, default='')
    sort_order = models.IntegerField(default=0)
    overview = models.TextField(
        blank=True, default='',
        help_text="Markdown overview shown on the module overview page (synced from README.md).",
    )
    overview_html = models.TextField(
        blank=True, default='',
        help_text="Auto-rendered HTML from overview markdown.",
    )
    overview_source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
        help_text="Source repo path of the README.md that backs the overview.",
    )
    is_bonus = models.BooleanField(
        default=False, db_default=False,
        help_text=(
            "Optional enrichment module (issue #1674). Excluded from the "
            "progress denominator; completion is still tracked/shown."
        ),
    )
    available_after_days = models.IntegerField(
        null=True, blank=True,
        help_text=(
            "Cohort drip offset for this module, same semantics as "
            "Unit.available_after_days (this many days after cohort "
            "start_date). Meaningful on any module; the real use is "
            "top-level ('week') modules, which also derive the cohort "
            "week date range shown to learners from this value. Issue "
            "#1674."
        ),
    )

    class Meta:
        ordering = ['sort_order']
        constraints = [
            # Issue #1674 (rescoped after #1675 grooming): slug uniqueness
            # is per SIBLING group, not course-wide. Real Maven content
            # repeats submodule slugs across weeks (a "Homework" submodule
            # under week 1, 3, 4, 5, 6; "(Overview)" submodules across
            # several weeks) — a course-wide constraint would reject that
            # import outright. Matches the community_base package's
            # equivalent (course, parent, slug) scoping.
            #
            # A plain ``UniqueConstraint(fields=['course', 'parent',
            # 'slug'])`` would NOT enforce uniqueness among top-level
            # modules: SQL unique constraints never treat two NULLs as
            # equal, so every top-level module has a distinct (course,
            # NULL, slug) tuple regardless of slug collisions. Two
            # explicit constraints close that gap: one scoped to
            # parent IS NULL (top-level siblings share a course), one
            # scoped to parent IS NOT NULL (submodule siblings share a
            # parent, which already pins the course).
            models.UniqueConstraint(
                fields=['course', 'slug'],
                condition=models.Q(parent__isnull=True),
                name='module_top_level_slug_unique_per_course',
            ),
            models.UniqueConstraint(
                fields=['parent', 'slug'],
                condition=models.Q(parent__isnull=False),
                name='module_submodule_slug_unique_per_parent',
            ),
        ]

    def __str__(self):
        return f'{self.course.title} - {self.title}'

    def get_absolute_url(self):
        """Return URL for this module's overview page (no trailing slash —
        the project uses ``RemoveTrailingSlashMiddleware``).

        Issue #1674 (resolved, not deferred, per the coordinator's
        correction): a top-level module (``parent_id`` is ``None``) keeps
        the existing, unchanged two-segment shape —
        ``/courses/<course>/<module>`` — exactly what every existing
        two-level course's indexed URLs already are; the slug scoping
        that makes this safe (``module_top_level_slug_unique_per_course``)
        was never relaxed for top-level modules.

        A submodule gets a NEW three-segment shape that embeds its
        parent — ``/courses/<course>/<parent>/<submodule>`` — because
        ``module_submodule_slug_unique_per_parent`` only guarantees a
        submodule's slug is unique among ITS OWN siblings, not
        course-wide (two submodules under different parent weeks may
        legally share a slug, e.g. "Homework" repeated across weeks in
        the real Maven content). Embedding the parent makes every
        submodule URL unique by construction — no ``MultipleObjectsReturned``
        is possible. See ``content.views.courses.course_unit_detail`` for
        how the three-segment path is routed to either this submodule
        -overview case or the unchanged two-level unit-detail case,
        deterministically (not a fallback guess) via ``Module.clean()``'s
        mixed-content invariant: a top-level module holds EITHER
        submodules OR direct units, never both, so a given first path
        segment can only ever mean one or the other.
        """
        if self.parent_id is None:
            return f'/courses/{self.course.slug}/{self.slug}'
        return f'/courses/{self.course.slug}/{self.parent.slug}/{self.slug}'

    def clean(self):
        super().clean()
        if self.parent_id is not None:
            if self.pk is not None and self.parent_id == self.pk:
                raise ValidationError({
                    'parent': 'A module cannot be its own parent.',
                })
            parent = self.parent
            if parent.parent_id is not None:
                raise ValidationError({
                    'parent': (
                        'A submodule cannot itself have children '
                        '(maximum two levels of module).'
                    ),
                })
            if parent.course_id != self.course_id:
                raise ValidationError({
                    'parent': 'Parent module must belong to the same course.',
                })
            if parent.units.exists():
                raise ValidationError({
                    'parent': (
                        f'"{parent.title}" already has direct units and '
                        'cannot also have submodules.'
                    ),
                })
        # Belt-and-braces symmetric check for rows that already exist —
        # catches the mixed-content rule even when a caller skips the
        # two creation-time guards above (Unit.clean / this branch).
        if self.pk is not None and self.children.exists() and self.units.exists():
            raise ValidationError(
                f'"{self.title}" cannot have both submodules and direct units.'
            )

    def save(self, *args, **kwargs):
        from content.utils.linkify import linkify_urls
        if self.overview:
            # Strip the leading H1 if it duplicates the module title — the
            # module overview page renders the title as the page heading,
            # so a README that starts with ``# Module Title`` would show
            # up twice (issue #222 / #227).
            overview_md = strip_leading_title_h1(self.overview, self.title)
            self.overview_html = linkify_urls(render_markdown(overview_md))
        else:
            self.overview_html = ''
        # When save() is called with update_fields, ensure overview_html is
        # included so it gets written to DB.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'overview' in update_fields:
                update_fields.add('overview_html')
            kwargs['update_fields'] = list(update_fields)
        super().save(*args, **kwargs)

    @property
    def is_leaf(self):
        """True when this module holds units directly (no children)."""
        if self.pk is None:
            return True
        return not self.children.exists()


UNIT_KIND_LESSON = 'lesson'
UNIT_KIND_HOMEWORK = 'homework'
UNIT_KIND_EVENT = 'event'

UNIT_KIND_CHOICES = [
    (UNIT_KIND_LESSON, 'Lesson'),
    (UNIT_KIND_HOMEWORK, 'Homework'),
    (UNIT_KIND_EVENT, 'Event'),
]


class Unit(SyncedContentIdentityMixin, SourceMetadataMixin, models.Model):
    """A single lesson unit within a module."""

    module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name='units',
    )
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, default='')
    sort_order = models.IntegerField(default=0)
    video_url = models.URLField(max_length=500, blank=True, default='')
    body = models.TextField(
        blank=True, default='',
        help_text="Markdown lesson text.",
    )
    body_html = models.TextField(
        blank=True, default='',
        help_text="Auto-rendered HTML from body markdown.",
    )
    homework = models.TextField(
        blank=True, default='',
        help_text="Markdown homework description.",
    )
    homework_html = models.TextField(
        blank=True, default='',
        help_text="Auto-rendered HTML from homework markdown.",
    )
    timestamps = models.JSONField(
        default=list, blank=True,
        help_text='List of {time_seconds, label} objects.',
    )
    is_preview = models.BooleanField(
        default=False,
        help_text="If true, visible to everyone regardless of course access.",
    )
    required_level = models.IntegerField(
        null=True, blank=True,
        choices=UNIT_VISIBILITY_CHOICES,
        help_text=(
            "Per-unit override of the course default. When null, the unit "
            "inherits Course.default_unit_required_level (or, when that is "
            "also null, Course.required_level). Issue #465."
        ),
    )
    available_after_days = models.IntegerField(
        null=True, blank=True,
        help_text="For cohort drip schedule: unit becomes available this many days after cohort start_date. Null = available immediately.",
    )
    content_hash = models.CharField(
        max_length=32, blank=True, null=True,
        help_text="MD5 hex digest of body text for rename detection.",
    )
    kind = models.CharField(
        max_length=20, choices=UNIT_KIND_CHOICES,
        default=UNIT_KIND_LESSON, db_default=UNIT_KIND_LESSON,
        help_text=(
            "Element type within the module (issue #1674). 'lesson' is "
            "the default so every existing row is correct with no data "
            "migration."
        ),
    )
    session_position = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=(
            "1-indexed position within the course's live-session series "
            "(matches events.Event.series_position). Meaningful only "
            "when kind='event'. NOT a FK — the actual Event is resolved "
            "per viewer/cohort at render time (issue #1674) since a "
            "stored FK would embed one cohort's event into curriculum "
            "every cohort shares."
        ),
    )
    is_bonus = models.BooleanField(
        default=False, db_default=False,
        help_text=(
            "Optional element, even inside a required module (issue "
            "#1674). Excluded from the progress denominator; completion "
            "is still tracked/shown."
        ),
    )

    class Meta:
        ordering = ['sort_order']
        unique_together = [('module', 'slug')]

    def __str__(self):
        return f'{self.module.title} - {self.title}'

    def clean(self):
        super().clean()
        if self.kind == UNIT_KIND_EVENT:
            if self.session_position is None or self.session_position < 1:
                raise ValidationError({
                    'session_position': (
                        'kind="event" requires a positive session_position.'
                    ),
                })
        if self.module_id is not None and self.module.children.exists():
            raise ValidationError({
                'module': (
                    f'"{self.module.title}" already has submodules and '
                    'cannot also have direct units.'
                ),
            })

    def save(self, *args, **kwargs):
        from content.utils.code_annotations import render_course_unit_body
        from content.utils.linkify import linkify_urls
        if self.body:
            # Strip the leading H1 if it duplicates the unit title — the
            # unit page renders the title as the page heading, so a body
            # that starts with ``# Unit Title`` would show up twice
            # (issue #227).
            body_md = strip_leading_title_h1(self.body, self.title)
            self.body_html = render_course_unit_body(body_md)
        else:
            self.body_html = ''
        if self.homework:
            self.homework_html = linkify_urls(render_markdown(self.homework))
        # When save() is called with update_fields (e.g. from update_or_create),
        # ensure rendered HTML fields are included so they get written to DB.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'body' in update_fields:
                update_fields.add('body_html')
            if 'homework' in update_fields:
                update_fields.add('homework_html')
            kwargs['update_fields'] = list(update_fields)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        """Return URL for this unit's page.

        Issue #1674: unchanged two-level shape
        (``/courses/<course>/<module>/<unit>``) when the unit's module is
        top-level (every existing course's unit URLs, byte-for-byte). A
        unit inside a submodule gets a new four-segment shape that embeds
        the parent too — ``/courses/<course>/<parent>/<submodule>/<unit>``
        — the same "the parent segment is what makes a submodule's
        namespace unambiguous" reasoning as ``Module.get_absolute_url()``.
        """
        course = self.module.course
        if self.module.parent_id is None:
            return f'/courses/{course.slug}/{self.module.slug}/{self.slug}'
        return (
            f'/courses/{course.slug}/{self.module.parent.slug}/'
            f'{self.module.slug}/{self.slug}'
        )

    def get_studio_edit_url(self):
        return f'/studio/units/{self.pk}/edit'

    @property
    def effective_required_level(self):
        """Resolve the access level for this unit (issue #465).

        Resolution order:
        1. ``Unit.required_level`` (per-unit YAML override) when set.
        2. ``Course.default_unit_required_level`` (course YAML default).
        3. ``Course.required_level`` (legacy fallback — pre-#465 behavior).

        ``is_preview`` is intentionally NOT collapsed into this property:
        callers (the unit detail view, the API endpoint, and ``can_access``
        for unit-shaped content) check ``is_preview`` first because the
        flag still drives template branches like the sidebar Preview
        badge. Treating it as a separate gate keeps that UI signal
        intact.
        """
        if self.required_level is not None:
            return self.required_level
        course = self.module.course
        if course.default_unit_required_level is not None:
            return course.default_unit_required_level
        return course.required_level


class UserCourseProgress(models.Model):
    """Tracks user progress through course units."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_progress',
    )
    unit = models.ForeignKey(
        Unit, on_delete=models.CASCADE, related_name='progress',
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('user', 'unit')]

    def __str__(self):
        status = 'completed' if self.completed_at else 'in progress'
        return f'{self.user} - {self.unit} ({status})'


ACCESS_TYPE_CHOICES = [
    ('purchased', 'Purchased'),
    ('granted', 'Granted'),
]


class CourseAccess(models.Model):
    """Individual course access granted via purchase or admin assignment."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_access',
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='individual_access',
    )
    access_type = models.CharField(
        max_length=20, choices=ACCESS_TYPE_CHOICES, default='purchased',
    )
    stripe_session_id = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Stripe checkout session ID (empty for granted access).",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='granted_course_access',
        help_text="Admin who granted access (null for purchased).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'course')]

    def __str__(self):
        return f'{self.user} - {self.course.title} ({self.access_type})'
