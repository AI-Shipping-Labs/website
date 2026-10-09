"""Pod, membership and join-request models (issue #1918).

A pod belongs to exactly one community activity. Phase 1 wires dated
course cohorts only; the ``sprint`` link is reserved for #1921 so sprint
pods need no schema change. The DB check constraint enforces that exactly
one activity link is set.

``PodJoinRequest`` is the one mechanism for both approval and the waiting
list: a request is ``pending`` while the pod has a free seat and
``waitlisted`` while it is full. The business rules live in
``pods.services.membership`` so member views, Studio and the staff API
share them.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from content.models.mixins import TimestampedModelMixin

__all__ = [
    'MEETING_MINUTES_CHOICES',
    'POD_SOURCE_API',
    'POD_SOURCE_CHOICES',
    'POD_SOURCE_MEMBER',
    'POD_SOURCE_STUDIO',
    'POD_STATUS_ARCHIVED',
    'POD_STATUS_CHOICES',
    'POD_STATUS_CLOSED',
    'POD_STATUS_OPEN',
    'MEMBERSHIP_SOURCE_API',
    'MEMBERSHIP_SOURCE_CHOICES',
    'MEMBERSHIP_SOURCE_CREATOR',
    'MEMBERSHIP_SOURCE_REQUEST',
    'MEMBERSHIP_SOURCE_STAFF',
    'OPEN_REQUEST_STATUSES',
    'REQUEST_STATUS_APPROVED',
    'REQUEST_STATUS_CANCELLED',
    'REQUEST_STATUS_CHOICES',
    'REQUEST_STATUS_DECLINED',
    'REQUEST_STATUS_PENDING',
    'REQUEST_STATUS_WAITLISTED',
    'REQUEST_STATUS_WITHDRAWN',
    'Pod',
    'PodJoinRequest',
    'PodMembership',
]

POD_STATUS_OPEN = 'open'
POD_STATUS_CLOSED = 'closed'
POD_STATUS_ARCHIVED = 'archived'
POD_STATUS_CHOICES = [
    (POD_STATUS_OPEN, 'Open'),
    (POD_STATUS_CLOSED, 'Closed'),
    (POD_STATUS_ARCHIVED, 'Archived'),
]

POD_SOURCE_MEMBER = 'member'
POD_SOURCE_STUDIO = 'studio'
POD_SOURCE_API = 'api'
POD_SOURCE_CHOICES = [
    (POD_SOURCE_MEMBER, 'Member'),
    (POD_SOURCE_STUDIO, 'Studio'),
    (POD_SOURCE_API, 'API'),
]

MEETING_MINUTES_CHOICES = [
    (30, '30 min'),
    (45, '45 min'),
    (60, '60 min'),
    (90, '90 min'),
]

MEMBERSHIP_SOURCE_CREATOR = 'creator'
MEMBERSHIP_SOURCE_REQUEST = 'request'
MEMBERSHIP_SOURCE_STAFF = 'staff'
MEMBERSHIP_SOURCE_API = 'api'
MEMBERSHIP_SOURCE_CHOICES = [
    (MEMBERSHIP_SOURCE_CREATOR, 'Creator'),
    (MEMBERSHIP_SOURCE_REQUEST, 'Request'),
    (MEMBERSHIP_SOURCE_STAFF, 'Staff'),
    (MEMBERSHIP_SOURCE_API, 'API'),
]

REQUEST_STATUS_PENDING = 'pending'
REQUEST_STATUS_WAITLISTED = 'waitlisted'
REQUEST_STATUS_APPROVED = 'approved'
REQUEST_STATUS_DECLINED = 'declined'
REQUEST_STATUS_WITHDRAWN = 'withdrawn'
REQUEST_STATUS_CANCELLED = 'cancelled'
REQUEST_STATUS_CHOICES = [
    (REQUEST_STATUS_PENDING, 'Pending'),
    (REQUEST_STATUS_WAITLISTED, 'Waitlisted'),
    (REQUEST_STATUS_APPROVED, 'Approved'),
    (REQUEST_STATUS_DECLINED, 'Declined'),
    (REQUEST_STATUS_WITHDRAWN, 'Withdrawn'),
    (REQUEST_STATUS_CANCELLED, 'Cancelled'),
]
OPEN_REQUEST_STATUSES = (REQUEST_STATUS_PENDING, REQUEST_STATUS_WAITLISTED)


class Pod(TimestampedModelMixin, models.Model):
    """A small group of members working on one purpose inside an activity."""

    cohort = models.ForeignKey(
        'content.Cohort',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='pods',
        help_text='Dated course cohort this pod belongs to.',
    )
    sprint = models.ForeignKey(
        'plans.Sprint',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='pods',
        help_text='Reserved for sprint pods (#1921); never set in phase 1.',
    )
    name = models.CharField(max_length=80)
    purpose = models.TextField(max_length=500)
    max_members = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    meeting_count = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
    )
    meeting_minutes = models.PositiveSmallIntegerField(
        default=60,
        choices=MEETING_MINUTES_CHOICES,
    )
    status = models.CharField(
        max_length=20,
        choices=POD_STATUS_CHOICES,
        default=POD_STATUS_OPEN,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='owned_pods',
        help_text='Member who approves requests. Empty: only staff decide.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_pods',
    )
    source = models.CharField(
        max_length=20,
        choices=POD_SOURCE_CHOICES,
        default=POD_SOURCE_MEMBER,
    )
    slack_channel_url = models.URLField(max_length=300, blank=True, default='')
    # One optional call link for every meeting of the pod (issue #1919).
    meeting_url = models.URLField(max_length=500, blank=True, default='', db_default='')

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(cohort__isnull=False, sprint__isnull=True)
                    | Q(cohort__isnull=True, sprint__isnull=False)
                ),
                name='pod_exactly_one_activity',
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def course(self):
        return self.cohort.course if self.cohort_id else None


class PodMembership(models.Model):
    """A member's seat in a pod."""

    pod = models.ForeignKey(Pod, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pod_memberships',
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    source = models.CharField(max_length=20, choices=MEMBERSHIP_SOURCE_CHOICES)

    class Meta:
        ordering = ['joined_at', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['pod', 'user'], name='pod_membership_unique_user'),
        ]

    def __str__(self):
        return f'{self.user} in {self.pod}'


class PodJoinRequest(models.Model):
    """A request to join a pod; also the waiting-list entry when it is full."""

    pod = models.ForeignKey(Pod, on_delete=models.CASCADE, related_name='join_requests')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pod_join_requests',
    )
    message = models.TextField(max_length=300, blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=REQUEST_STATUS_CHOICES,
        default=REQUEST_STATUS_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    # Set when the daily staff Slack alert (issue #1927) first announced
    # this request as stale, so each request is announced as new only once.
    stale_alerted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['pod', 'user'],
                condition=Q(status__in=OPEN_REQUEST_STATUSES),
                name='pod_request_one_open_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.user} -> {self.pod} ({self.status})'

    @property
    def is_open(self):
        return self.status in OPEN_REQUEST_STATUSES
