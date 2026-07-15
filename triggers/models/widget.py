"""The content-embeddable claim widget definition (issue #1070).

An ``EventWidget`` is dropped into synced markdown via the ``eventwidget``
shortcode (expanded at render time to a stable ``data-event-widget``
placeholder). Per-user state is hydrated client-side from an authed
endpoint; the claim POST calls ``emit_event`` server-side after enforcing
``min_level``. v0 credits is only the first widget — each future
partnership is a new row.
"""

from django.core.exceptions import ValidationError
from django.db import models

from content.access import LEVEL_REGISTERED


class EventWidget(models.Model):
    """A claim widget referenced by the markdown ``eventwidget`` shortcode."""

    slug = models.SlugField(
        unique=True,
        help_text="Referenced by the markdown shortcode, e.g. 'v0-claim'.",
    )
    event_name = models.CharField(
        max_length=100,
        help_text="The name passed to emit_event when claimed (e.g. 'v0_workshop').",
    )
    min_level = models.IntegerField(
        default=LEVEL_REGISTERED,
        help_text="Minimum access level required to claim (server-enforced).",
    )
    claim_label = models.CharField(
        max_length=120,
        default="Claim",
        help_text="Button label shown in the claimable state.",
    )
    claim_body = models.TextField(
        blank=True,
        default="",
        help_text="Supporting copy shown in the claimable state.",
    )
    signin_cta = models.CharField(
        max_length=120,
        default="Sign in to claim",
        help_text="CTA label shown to anonymous visitors.",
    )
    claimed_label = models.CharField(
        max_length=120,
        default="Claimed",
        help_text="Label shown once the user has claimed.",
    )
    exhausted_label = models.CharField(
        max_length=120,
        default="No longer available",
        help_text="Label for the exhausted state (reserved for Phase 2).",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        verbose_name = "Event widget"
        verbose_name_plural = "Event widgets"

    def __str__(self):
        return self.slug

    def clean(self):
        super().clean()
        if self.min_level not in {0, 5, 10, 20, 30}:
            raise ValidationError(
                {"min_level": "Choose one of the supported access levels: 0, 5, 10, 20, 30."},
            )

    @property
    def embed_shortcode(self):
        """The exact fenced shortcode an author copies into markdown."""
        return f"```eventwidget\nslug: {self.slug}\n```"
