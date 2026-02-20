import markdown as md_lib

from django.conf import settings
from django.db import models

from content.access import VISIBILITY_CHOICES, get_required_tier_name


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
    is_free = models.BooleanField(
        default=False,
        help_text="True for lead-magnet courses (required_level = 0).",
    )
    discussion_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text="Slack channel URL for paid courses, GitHub URL for free courses.",
    )
    tags = models.JSONField(default=list, blank=True)
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/courses/{self.slug}'

    def save(self, *args, **kwargs):
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)

        if self.description:
            self.description_html = render_markdown(self.description)
        super().save(*args, **kwargs)

    @property
    def is_published(self):
        return self.status == 'published'

    @property
    def required_tier_name(self):
        return get_required_tier_name(self.required_level)

    def total_units(self):
        """Return the total number of units in this course."""
        return Unit.objects.filter(module__course=self).count()

    def completed_units(self, user):
        """Return the number of units completed by the given user."""
        if user is None or not user.is_authenticated:
            return 0
        return UserCourseProgress.objects.filter(
            user=user,
            unit__module__course=self,
            completed_at__isnull=False,
        ).count()

    def get_syllabus(self):
        """Return modules with their units, ordered by sort_order."""
        modules = self.modules.prefetch_related('units').order_by('sort_order')
        return modules


class Module(models.Model):
    """A module within a course, containing units."""

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name='modules',
    )
    title = models.CharField(max_length=300)
    sort_order = models.IntegerField(default=0)
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

    def __str__(self):
        return f'{self.course.title} - {self.title}'


class Unit(models.Model):
    """A single lesson unit within a module."""

    module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name='units',
    )
    title = models.CharField(max_length=300)
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

    def __str__(self):
        return f'{self.module.title} - {self.title}'

    def save(self, *args, **kwargs):
        if self.body:
            self.body_html = render_markdown(self.body)
        if self.homework:
            self.homework_html = render_markdown(self.homework)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        """Return URL for this unit's page."""
        course = self.module.course
        return f'/courses/{course.slug}/{self.module.sort_order}/{self.sort_order}'


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
