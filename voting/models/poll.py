import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_PREMIUM


POLL_TYPE_CHOICES = [
    ('topic', 'Topic'),
    ('course', 'Mini-course'),
]

POLL_STATUS_CHOICES = [
    ('open', 'Open'),
    ('closed', 'Closed'),
]

# Auto-set required_level based on poll_type
POLL_TYPE_LEVEL_MAP = {
    'topic': LEVEL_MAIN,    # 20
    'course': LEVEL_PREMIUM,  # 30
}


class Poll(models.Model):
    """A poll where members can vote on future content topics or courses."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default='')
    poll_type = models.CharField(
        max_length=20,
        choices=POLL_TYPE_CHOICES,
        default='topic',
        help_text='Topic polls require Main+, course polls require Premium.',
    )
    required_level = models.IntegerField(
        default=LEVEL_MAIN,
        help_text='Auto-set based on poll_type: 20 for topic, 30 for course.',
    )
    status = models.CharField(
        max_length=20,
        choices=POLL_STATUS_CHOICES,
        default='open',
    )
    allow_proposals = models.BooleanField(
        default=False,
        help_text='If true, members can propose new options.',
    )
    max_votes_per_user = models.IntegerField(
        default=3,
        help_text='Maximum number of options a user can vote on.',
    )
    closes_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Optional auto-close datetime. Null = stays open until manually closed.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # Auto-set required_level based on poll_type
        self.required_level = POLL_TYPE_LEVEL_MAP.get(
            self.poll_type, LEVEL_MAIN,
        )
        super().save(*args, **kwargs)

    @property
    def is_closed(self):
        """Return True if the poll is closed (status or past closes_at)."""
        if self.status == 'closed':
            return True
        if self.closes_at and timezone.now() >= self.closes_at:
            return True
        return False

    @property
    def total_votes(self):
        """Return the total number of votes cast on this poll."""
        return PollVote.objects.filter(poll=self).count()

    @property
    def options_count(self):
        """Return the number of options for this poll."""
        return self.options.count()

    def get_absolute_url(self):
        return f'/vote/{self.id}'


class PollOption(models.Model):
    """An option within a poll that users can vote on."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll = models.ForeignKey(
        Poll,
        on_delete=models.CASCADE,
        related_name='options',
    )
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default='')
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='proposed_options',
        help_text='Null if created by admin.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.title

    @property
    def vote_count(self):
        """Return the number of votes for this option."""
        return self.votes.count()


class PollVote(models.Model):
    """A user's vote on a specific poll option."""

    poll = models.ForeignKey(
        Poll,
        on_delete=models.CASCADE,
        related_name='votes',
    )
    option = models.ForeignKey(
        PollOption,
        on_delete=models.CASCADE,
        related_name='votes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='poll_votes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('poll', 'user', 'option')]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} -> {self.option}'
