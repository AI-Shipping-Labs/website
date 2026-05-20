"""Event feedback model.

Issue #679: registered attendees can rate and comment on an event after
it ends. One row per (event, user) — re-submissions overwrite via
``update_or_create``. Rating is nullable so an attendee can leave a
comment-only entry; comment-only rows are excluded from the public
average.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class EventFeedback(models.Model):
    """Post-event feedback left by a registered attendee."""

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='feedback',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_feedback',
    )
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='1-5 star rating. Nullable so attendees can leave a comment-only entry.',
    )
    comment = models.TextField(
        blank=True, default='',
        help_text='Free-form feedback from the attendee.',
    )
    would_change = models.TextField(
        blank=True, default='',
        help_text='What would you change next time?',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('event', 'user')]
        ordering = ['-created_at']

    def __str__(self):
        rating = f'{self.rating}*' if self.rating else 'no rating'
        return f'{self.user} - {self.event} ({rating})'

    def clean(self):
        """Reject rows where rating, comment, and would_change are all empty."""
        super().clean()
        has_rating = self.rating is not None
        has_comment = bool((self.comment or '').strip())
        has_would_change = bool((self.would_change or '').strip())
        if not (has_rating or has_comment or has_would_change):
            raise ValidationError(
                'Please leave a rating or a comment.'
            )
