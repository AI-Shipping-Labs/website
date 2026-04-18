import markdown as md_lib
from django.conf import settings
from django.db import models

from content.access import VISIBILITY_CHOICES, get_required_tier_name
from content.utils.h1 import strip_leading_title_h1


def render_markdown(text):
    """Convert markdown to HTML with syntax highlighting."""
    return md_lib.markdown(
        text,
        extensions=[
            'fenced_code',
            'codehilite',
            'tables',
            'attr_list',
            'md_in_html',
        ],
        extension_configs={
            'codehilite': {
                'css_class': 'codehilite',
                'guess_lang': False,
            },
        },
    )


STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('published', 'Published'),
]


class Course(models.Model):
    """Structured course: Course -> Modules -> Units."""

    content_id = models.UUIDField(
        unique=True, null=True, blank=True,
        help_text="Stable UUID from frontmatter for linking user-generated data.",
    )
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
    instructor_name = models.CharField(max_length=200, blank=True, default='')
    instructor_bio = models.TextField(blank=True, default='')
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to access course units.",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
    )
    discussion_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text="Slack channel URL for paid courses, GitHub URL for free courses.",
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
    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
        help_text="GitHub repo this content was synced from.",
    )
    source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
        help_text="File path within the source repo.",
    )
    source_commit = models.CharField(
        max_length=40, blank=True, null=True, default=None,
        help_text="Git commit SHA of the last sync.",
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/courses/{self.slug}'

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
    def is_free(self) -> bool:
        """True when the course has no tier requirement (lead magnet)."""
        return self.required_level == 0

    @property
    def required_tier_name(self):
        return get_required_tier_name(self.required_level)

    def total_units(self):
        """Return the total number of units in this course.

        Excludes legacy README-as-unit rows (slug='readme', sort_order=-1)
        that may still exist in databases not yet migrated. After the
        backfill migration these rows are gone, so the exclusion is a
        no-op in normal operation.
        """
        return Unit.objects.filter(module__course=self).exclude(
            slug='readme', sort_order=-1,
        ).count()

    def completed_units(self, user):
        """Return the number of units completed by the given user.

        Excludes legacy README-as-unit rows so the count lines up with
        ``total_units()`` for progress percentages.
        """
        if user is None or not user.is_authenticated:
            return 0
        return UserCourseProgress.objects.filter(
            user=user,
            unit__module__course=self,
            completed_at__isnull=False,
        ).exclude(unit__slug='readme', unit__sort_order=-1).count()

    def get_syllabus(self):
        """Return modules with their units, ordered by sort_order."""
        modules = self.modules.prefetch_related('units').order_by('sort_order')
        return modules

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

        This walks the course's units with two queries (units +
        completed-progress ids). Callers that need to compute this for
        many courses should batch-prefetch the data and resolve next-unit
        in Python — see ``content.views.home._get_in_progress_courses``.
        """
        if user is None or not user.is_authenticated:
            return None
        units = list(
            Unit.objects.filter(module__course=self)
            .exclude(slug='readme', sort_order=-1)
            .select_related('module')
            .order_by('module__sort_order', 'sort_order')
        )
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


class Module(models.Model):
    """A module within a course, containing units."""

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='modules',
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
    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
        help_text="GitHub repo this content was synced from.",
    )
    source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
        help_text="File path within the source repo.",
    )
    source_commit = models.CharField(
        max_length=40, blank=True, null=True, default=None,
        help_text="Git commit SHA of the last sync.",
    )
    overview_source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
        help_text="Source repo path of the README.md that backs the overview.",
    )

    class Meta:
        ordering = ['sort_order']
        unique_together = [('course', 'slug')]

    def __str__(self):
        return f'{self.course.title} - {self.title}'

    def get_absolute_url(self):
        """Return URL for this module's overview page (no trailing slash —
        the project uses ``RemoveTrailingSlashMiddleware``)."""
        return f'/courses/{self.course.slug}/{self.slug}'

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


class Unit(models.Model):
    """A single lesson unit within a module."""

    content_id = models.UUIDField(
        unique=True, null=True, blank=True,
        help_text="Stable UUID from frontmatter for linking user-generated data.",
    )
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
    available_after_days = models.IntegerField(
        null=True, blank=True,
        help_text="For cohort drip schedule: unit becomes available this many days after cohort start_date. Null = available immediately.",
    )
    content_hash = models.CharField(
        max_length=32, blank=True, null=True,
        help_text="MD5 hex digest of body text for rename detection.",
    )
    source_repo = models.CharField(
        max_length=300, blank=True, null=True, default=None,
        help_text="GitHub repo this content was synced from.",
    )
    source_path = models.CharField(
        max_length=500, blank=True, null=True, default=None,
        help_text="File path within the source repo.",
    )
    source_commit = models.CharField(
        max_length=40, blank=True, null=True, default=None,
        help_text="Git commit SHA of the last sync.",
    )

    class Meta:
        ordering = ['sort_order']
        unique_together = [('module', 'slug')]

    def __str__(self):
        return f'{self.module.title} - {self.title}'

    def save(self, *args, **kwargs):
        from content.utils.linkify import linkify_urls
        if self.body:
            # Strip the leading H1 if it duplicates the unit title — the
            # unit page renders the title as the page heading, so a body
            # that starts with ``# Unit Title`` would show up twice
            # (issue #227).
            body_md = strip_leading_title_h1(self.body, self.title)
            self.body_html = linkify_urls(render_markdown(body_md))
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
        """Return URL for this unit's page."""
        course = self.module.course
        return f'/courses/{course.slug}/{self.module.slug}/{self.slug}'


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
