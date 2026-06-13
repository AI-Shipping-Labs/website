"""UserActivity: per-user curated activity timeline for the CRM (issue #853).

One row per recorded meaningful action by an authenticated user. This is a
lightweight, queryable, server-side record of a small curated set of
high-signal actions — NOT a raw clickstream (that is the deferred #773
firehose). See the issue for the full scope and the deliberate Phase 1 /
Phase 2 split.

Privacy: the model stores NO raw IP, NO user-agent, and NO full URL with
querystring. ``object_id`` / ``target_url`` are internal identifiers /
Studio deep links only.
"""

from django.conf import settings
from django.db import models


class UserActivity(models.Model):
    """A single recorded meaningful action by an authenticated user.

    Lives in the ``analytics`` app alongside ``CampaignVisit`` /
    ``UserAttribution``. The denormalised ``label`` / ``target_url`` let
    the Studio timeline render without N+1 joins and survive deletion of
    the source object (``object_id`` is a CharField, not an FK, so deleting
    the target does not cascade-delete history).
    """

    EVENT_SIGNUP = 'signup'
    EVENT_COURSE_ENROLL = 'course_enroll'
    EVENT_LESSON_OPEN = 'lesson_open'
    EVENT_EVENT_REGISTER = 'event_register'
    EVENT_EVENT_JOIN = 'event_join'
    EVENT_PAYMENT = 'payment'
    EVENT_EMAIL_CLICK = 'email_click'
    EVENT_SLACK_JOIN = 'slack_join_click'

    EVENT_TYPE_CHOICES = [
        (EVENT_SIGNUP, 'Signup'),
        (EVENT_COURSE_ENROLL, 'Enrolled'),
        (EVENT_LESSON_OPEN, 'Lesson'),
        (EVENT_EVENT_REGISTER, 'Registered'),
        (EVENT_EVENT_JOIN, 'Joined'),
        (EVENT_PAYMENT, 'Payment'),
        (EVENT_EMAIL_CLICK, 'Email click'),
        (EVENT_SLACK_JOIN, 'Slack join'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='activities',
        db_index=True,
    )
    event_type = models.CharField(
        max_length=40,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
    )
    occurred_at = models.DateTimeField(
        db_index=True,
        help_text='When the action happened. For backfilled/derived rows '
                  'this is set to the source timestamp so the timeline is '
                  'chronologically correct.',
    )
    object_type = models.CharField(
        max_length=40,
        blank=True,
        default='',
        help_text='Optional content-type label, e.g. course, unit, event, campaign.',
    )
    object_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text='Optional identifier (slug or pk) of the related object.',
    )
    label = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='Human-readable summary rendered in the timeline. '
                  'Denormalised on write so the timeline survives target deletion.',
    )
    target_url = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='Optional Studio-side deep link to the related object. '
                  'Blank when there is no sensible Studio target.',
    )

    class Meta:
        verbose_name = 'User Activity'
        verbose_name_plural = 'User Activities'
        ordering = ['-occurred_at']
        indexes = [
            models.Index(
                fields=['user', '-occurred_at'],
                name='analytics_activity_user_ts_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user_id} {self.event_type} @ {self.occurred_at:%Y-%m-%d %H:%M}'


__all__ = ['UserActivity']
