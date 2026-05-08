"""Workshop content type (issue #295).

A Workshop is a multi-page learning artifact (pages + linked YouTube recording +
optional code folder) that doesn't fit into ``Article``, ``Course``, or
``Event`` on its own. It is synced from the public ``AI-Shipping-Labs/workshops-content``
repo and exposes a split gating rule: the page content is available at one
tier level, the recording is available at an equal-or-higher tier level.

Public ``/workshops/`` views are intentionally out of scope for this issue —
they will be added in a follow-up. This module ships models + the helper
methods the sync pipeline and admin need.
"""

from django.core.exceptions import ValidationError
from django.db import models

from content.access import (
    LEVEL_BASIC,
    LEVEL_OPEN,
    LEVEL_REGISTERED,
    UNIT_VISIBILITY_CHOICES,
    VISIBILITY_CHOICES,
    get_user_level,
)
from content.models.mixins import (
    SourceMetadataMixin,
    SyncedContentIdentityMixin,
    TimestampedModelMixin,
)
from content.utils.markdown import render_markdown

STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('published', 'Published'),
]


def _can_access_level(user, required_level):
    """Decide whether ``user`` clears a workshop gate at ``required_level``.

    Mirrors :func:`content.access.can_access` semantics for the three
    workshop gate fields without going through a Content instance:

    - ``LEVEL_OPEN``: anonymous + paid tier allowed; free verified
      allowed; free unverified blocked.
    - ``LEVEL_REGISTERED``: anonymous denied; any tier allowed when
      email is verified or the user is already on a paid tier.
    - Numeric ``>= LEVEL_BASIC`` gates: pure level comparison.
    """
    if required_level == LEVEL_OPEN:
        if user is None or not user.is_authenticated:
            return True
        if get_user_level(user) >= LEVEL_BASIC:
            return True
        return bool(user.email_verified)
    if required_level == LEVEL_REGISTERED:
        if user is None or not user.is_authenticated:
            return False
        if get_user_level(user) >= LEVEL_BASIC:
            return True
        return bool(user.email_verified)
    return get_user_level(user) >= required_level


class Workshop(
    SyncedContentIdentityMixin,
    SourceMetadataMixin,
    TimestampedModelMixin,
    models.Model,
):
    """A multi-page workshop with an optional linked recording.

    A Workshop is a synced content type keyed by ``content_id`` (stable UUID
    from ``workshop.yaml``) and ``slug``. Its pages live in
    :class:`WorkshopPage` rows; the recording, timestamps, and materials live
    on a linked :class:`events.Event` row (so workshops reuse the recording
    rendering pipeline without duplicating fields).

    Gating is split across three levels, forming a monotonically-increasing
    chain that must hold at all times:

    - ``landing_required_level`` gates the workshop landing page (title,
      description, metadata). Typically ``0`` so free visitors can see what
      the workshop is about before signing up.
    - ``pages_required_level`` gates the tutorial page content, and must be
      ``>= landing_required_level``.
    - ``recording_required_level`` gates the recording, and must be
      ``>= pages_required_level``.

    The invariant
    ``landing_required_level <= pages_required_level <= recording_required_level``
    is validated in :meth:`clean`, :meth:`save`, and the sync parser. Fails
    closed so the recording is never leaked under a looser gate.
    """

    slug = models.SlugField(max_length=300, unique=True)
    title = models.CharField(max_length=300)
    description = models.TextField(
        blank=True, default='',
        help_text='Markdown description shown on the workshop landing page.',
    )
    description_html = models.TextField(
        blank=True, default='',
        help_text='Auto-rendered HTML from description markdown.',
    )
    date = models.DateField(
        help_text='Workshop date (used for ordering and the auto-created Event).',
    )
    instructors = models.ManyToManyField(
        'content.Instructor',
        through='content.WorkshopInstructor',
        related_name='workshops',
        blank=True,
        help_text=(
            'Instructors teaching this workshop. Order is controlled via the '
            'WorkshopInstructor.position field; the first instructor is the '
            'primary instructor shown on listings and cards.'
        ),
    )
    tags = models.JSONField(default=list, blank=True)
    cover_image_url = models.URLField(max_length=500, blank=True, default='')
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
    )
    landing_required_level = models.IntegerField(
        default=0,
        choices=VISIBILITY_CHOICES,
        help_text=(
            'Minimum tier level required to view the workshop landing page. '
            'Must be <= pages_required_level.'
        ),
    )
    pages_required_level = models.IntegerField(
        default=10,
        choices=UNIT_VISIBILITY_CHOICES,
        help_text=(
            'Minimum tier level required to view workshop pages. Accepts '
            'LEVEL_REGISTERED (5) so authors can require a free account '
            'without requiring payment.'
        ),
    )
    recording_required_level = models.IntegerField(
        default=20,
        choices=VISIBILITY_CHOICES,
        help_text=(
            'Minimum tier level required to watch the recording. Must be '
            '>= pages_required_level.'
        ),
    )
    code_repo_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text='GitHub folder URL with the workshop code.',
    )
    event = models.OneToOneField(
        'events.Event',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='workshop',
        help_text='Linked Event row that carries the recording metadata.',
    )
    class Meta:
        ordering = ['-date']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        """Forward-compat URL for the (not-yet-built) public workshop page."""
        return f'/workshops/{self.slug}'

    def clean(self):
        """Validate the three-gate chain landing <= pages <= recording."""
        super().clean()
        if self.landing_required_level > self.pages_required_level:
            raise ValidationError({
                'landing_required_level': (
                    'Landing gate must be at most as strict as the page '
                    'gate (landing_required_level <= pages_required_level).'
                ),
            })
        if self.recording_required_level < self.pages_required_level:
            raise ValidationError({
                'recording_required_level': (
                    'Recording gate must be at least as strict as the page '
                    'gate (recording_required_level >= pages_required_level).'
                ),
            })

    def save(self, *args, **kwargs):
        """Normalize tags, validate gate ordering, render description markdown."""
        from content.utils.tags import normalize_tags

        self.tags = normalize_tags(self.tags)

        # Validate gate ordering on every save so both admin and sync go
        # through the same invariant. ValidationError is the Django
        # conventional way to signal bad data here; callers that want to
        # catch it can wrap the save in a try/except.
        if self.landing_required_level > self.pages_required_level:
            raise ValidationError({
                'landing_required_level': (
                    'Landing gate must be at most as strict as the page '
                    'gate (landing_required_level <= pages_required_level).'
                ),
            })
        if self.recording_required_level < self.pages_required_level:
            raise ValidationError({
                'recording_required_level': (
                    'Recording gate must be at least as strict as the page '
                    'gate (recording_required_level >= pages_required_level).'
                ),
            })

        if self.description:
            self.description_html = render_markdown(self.description)
        else:
            self.description_html = ''

        super().save(*args, **kwargs)

    @property
    def ordered_instructors(self):
        """Return ``Instructor`` rows in ``WorkshopInstructor.position`` order."""
        return list(self.instructors.order_by('workshopinstructor__position'))

    @property
    def primary_instructor(self):
        """First instructor by position, or ``None`` when unset."""
        return self.instructors.order_by(
            'workshopinstructor__position',
        ).first()

    def user_can_access_landing(self, user):
        """Return True when ``user`` may view the workshop landing."""
        return _can_access_level(user, self.landing_required_level)

    def user_can_access_pages(self, user):
        """Return True when ``user`` may view the workshop tutorial pages.

        Honours ``LEVEL_REGISTERED``: anonymous visitors are denied, any
        authenticated user is allowed if their email is verified or they
        already hold a paid tier.
        """
        return _can_access_level(user, self.pages_required_level)

    def user_can_access_recording(self, user):
        """Return True when ``user`` may watch the workshop recording."""
        return _can_access_level(user, self.recording_required_level)


class WorkshopPage(
    SyncedContentIdentityMixin,
    SourceMetadataMixin,
    TimestampedModelMixin,
    models.Model,
):
    """A single markdown page within a workshop, ordered by ``sort_order``."""

    workshop = models.ForeignKey(
        Workshop, on_delete=models.CASCADE, related_name='pages',
    )
    slug = models.SlugField(max_length=300)
    title = models.CharField(max_length=300)
    sort_order = models.IntegerField(default=0)
    body = models.TextField(
        blank=True, default='',
        help_text='Markdown body of the page.',
    )
    body_html = models.TextField(
        blank=True, default='',
        help_text='Auto-rendered HTML from body markdown.',
    )
    video_start = models.CharField(
        max_length=10, blank=True, default='',
        help_text=(
            'Optional MM:SS or H:MM:SS timestamp marking where this page '
            'begins in the linked workshop recording. When set, a "Watch '
            'this section" link is shown above the page title for users '
            'with recording access.'
        ),
    )
    class Meta:
        ordering = ['sort_order']
        unique_together = [('workshop', 'slug')]

    def __str__(self):
        return f'{self.workshop.title} — {self.title}'

    def get_absolute_url(self):
        """Public URL for this tutorial page within its workshop."""
        return f'/workshops/{self.workshop.slug}/tutorial/{self.slug}'

    def save(self, *args, **kwargs):
        """Render body markdown to HTML on save."""
        if self.body:
            self.body_html = render_markdown(self.body)
        else:
            self.body_html = ''

        # Honour update_fields (some sync paths use it).
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'body' in update_fields:
                update_fields.add('body_html')
            kwargs['update_fields'] = list(update_fields)
        super().save(*args, **kwargs)
