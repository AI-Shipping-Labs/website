"""Database override for a transactional email template (issue #455).

Each row overrides one of the on-disk templates in
``email_app/email_templates/`` by ``template_name``. ``EmailService`` checks
for an override before reading the file, so the editor in Studio is purely
additive: deleting the row reverts to the filesystem template.
"""

from django.conf import settings
from django.db import models


class EmailTemplateOverride(models.Model):
    """Operator-edited override of one filesystem template.

    The ``template_name`` is the slug of the on-disk template, e.g.
    ``welcome`` or ``email_verification``. ``subject`` and
    ``body_markdown`` shadow the YAML frontmatter subject and the markdown
    body respectively. ``footer_note`` is appended to the chrome footer
    block on render.
    """

    template_name = models.SlugField(
        max_length=64,
        unique=True,
        help_text=(
            'Slug of the on-disk template this row overrides '
            '(e.g. "welcome", "email_verification").'
        ),
    )
    subject = models.CharField(
        max_length=200,
        help_text='Overrides the subject from the file frontmatter.',
    )
    body_markdown = models.TextField(
        help_text='Overrides the markdown body of the file template.',
    )
    footer_note = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text=(
            'Per-template footer note appended to the email chrome. '
            'Useful for "P.S." messages or per-email disclaimers.'
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text='Last operator who saved this override.',
    )

    class Meta:
        ordering = ['template_name']

    def __str__(self):
        return f'EmailTemplateOverride({self.template_name})'
