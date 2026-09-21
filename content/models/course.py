"""Course catalog rows come from community_base.curriculum.

This module re-exports package Course/Module/Unit and keeps AISL-only
overlay models (entitlement/Maven fields, progress, individual access).
"""

from community_base.curriculum.models import (  # noqa: F401
    STATUS_CHOICES,
    UNIT_KIND_CHECKLIST_ITEM,
    UNIT_KIND_EVENT,
    UNIT_KIND_HOMEWORK,
    UNIT_KIND_LESSON,
    Course,
    Module,
    Unit,
)
from django.conf import settings
from django.db import models

from content.utils.markdown import render_markdown  # noqa: F401

UNIT_KIND_CHOICES = [
    (UNIT_KIND_LESSON, 'Lesson'),
    (UNIT_KIND_HOMEWORK, 'Homework'),
    (UNIT_KIND_EVENT, 'Event'),
    (UNIT_KIND_CHECKLIST_ITEM, 'Checklist item'),
]

ACCESS_MODE_TIER = 'tier'
ACCESS_MODE_ENTITLEMENT = 'entitlement'
ACCESS_MODE_CHOICES = [
    (ACCESS_MODE_TIER, 'Tier-gated'),
    (ACCESS_MODE_ENTITLEMENT, 'Entitlement-only'),
]

READER_NAVIGATION_SCOPE_COURSE = 'course'
READER_NAVIGATION_SCOPE_SUBMODULE = 'submodule'
READER_NAVIGATION_SCOPE_MODULE = 'module'
READER_NAVIGATION_SCOPE_CHOICES = [
    (READER_NAVIGATION_SCOPE_COURSE, 'Entire course'),
    (READER_NAVIGATION_SCOPE_MODULE, 'Current module'),
    (READER_NAVIGATION_SCOPE_SUBMODULE, 'Current module (legacy)'),
]


def non_bonus_units(queryset):
    """Exclude bonus units/modules from ``queryset`` (issue #1674)."""
    return queryset.exclude(
        models.Q(is_bonus=True)
        | models.Q(module__is_bonus=True)
        | models.Q(module__parent__is_bonus=True)
    )


class CourseExtension(models.Model):
    """AISL-only fields that the shared Course model does not carry."""

    course = models.OneToOneField(
        Course,
        on_delete=models.CASCADE,
        related_name='aisl_extension',
    )
    maven_course_key = models.CharField(max_length=255, blank=True, default='', db_default='')
    source_repo = models.CharField(max_length=300, blank=True, default='', db_default='')
    access_mode = models.CharField(
        max_length=20,
        choices=ACCESS_MODE_CHOICES,
        default=ACCESS_MODE_TIER,
        db_default=ACCESS_MODE_TIER,
    )
    enroll_url = models.URLField(max_length=500, blank=True, default='', db_default='')
    program_label = models.CharField(max_length=100, blank=True, default='', db_default='')
    reader_navigation_scope = models.CharField(
        max_length=20,
        choices=READER_NAVIGATION_SCOPE_CHOICES,
        default=READER_NAVIGATION_SCOPE_COURSE,
        db_default=READER_NAVIGATION_SCOPE_COURSE,
    )
    individual_price_eur = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
    )
    stripe_product_id = models.CharField(max_length=255, blank=True, default='')
    stripe_price_id = models.CharField(max_length=255, blank=True, default='')
    auto_banner_title_hash = models.CharField(max_length=64, blank=True, default='')
    peer_review_enabled = models.BooleanField(default=False)
    peer_review_count = models.IntegerField(default=3)
    peer_review_deadline_days = models.IntegerField(default=7)
    peer_review_criteria = models.TextField(blank=True, default='')
    peer_review_criteria_html = models.TextField(blank=True, default='')

    def __str__(self):
        return f'AISL extension for {self.course.slug}'


class UserCourseProgress(models.Model):
    """Tracks user progress through course units."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_progress',
    )
    unit = models.ForeignKey(
        Unit, on_delete=models.CASCADE, related_name='aisl_progress',
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
