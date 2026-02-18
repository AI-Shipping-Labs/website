from django.db import models


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
    published = models.BooleanField(default=True)
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
