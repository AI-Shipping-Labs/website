from django.conf import settings
from django.db import models
from django.utils import timezone

from content.access import VISIBILITY_CHOICES


PROJECT_STATUS_CHOICES = [
    ('pending_review', 'Pending Review'),
    ('published', 'Published'),
]


class Project(models.Model):
    """Project idea / portfolio project."""
    DIFFICULTY_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    ]

    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    description = models.TextField(blank=True, default='')
    content_markdown = models.TextField(blank=True, default='')
    content_html = models.TextField(blank=True, default='')
    date = models.DateField()
    author = models.CharField(max_length=200, blank=True, default='')
    tags = models.JSONField(default=list, blank=True)
    reading_time = models.CharField(max_length=50, blank=True, default='')
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, blank=True, default='')
    estimated_time = models.CharField(max_length=100, blank=True, default='')
    source_code_url = models.URLField(max_length=500, blank=True, default='')
    demo_url = models.URLField(max_length=500, blank=True, default='')
    cover_image_url = models.URLField(max_length=500, blank=True, default='')
    required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to view full content.",
    )
    status = models.CharField(
        max_length=20,
        choices=PROJECT_STATUS_CHOICES,
        default='published',
    )
    published = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submitted_projects',
        help_text="User who submitted this project (community submissions).",
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return f'/projects/{self.slug}'

    def formatted_date(self):
        return self.date.strftime('%B %d, %Y')

    def short_date(self):
        return self.date.strftime('%b %d, %Y')

    def difficulty_color(self):
        colors = {
            'beginner': 'bg-green-500/20 text-green-400',
            'intermediate': 'bg-yellow-500/20 text-yellow-400',
            'advanced': 'bg-red-500/20 text-red-400',
        }
        return colors.get(self.difficulty, 'bg-secondary text-muted-foreground')

    def save(self, *args, **kwargs):
        # Normalize tags on save
        from content.utils.tags import normalize_tags
        self.tags = normalize_tags(self.tags)

        # Keep status in sync with published flag.
        if self.published:
            self.status = 'published'
            if not self.published_at:
                self.published_at = timezone.now()
        else:
            if self.status != 'pending_review':
                self.status = 'pending_review'
        super().save(*args, **kwargs)

    def approve(self):
        """Approve a pending project submission, publishing it."""
        self.published = True
        self.status = 'published'
        self.published_at = timezone.now()
        self.save()

    def reject(self):
        """Reject a pending project submission."""
        self.published = False
        self.status = 'pending_review'
        self.save()
