from django.db import models

from content.access import LEVEL_BASIC, VISIBILITY_CHOICES
from content.models.mixins import SourceMetadataMixin, TimestampedModelMixin


class InterviewCompany(SourceMetadataMixin, TimestampedModelMixin, models.Model):
    """A company interview process synced from the content repo.

    One row per ``interview-companies/<slug>.yaml`` file (issue #1712):
    the merged interview process for one company, converted from the
    field guide's per-posting job-description YAMLs. Uses the same mixin
    set as ``InterviewCategory`` so source attribution and sync identity
    work unchanged.
    """

    slug = models.SlugField(max_length=300, unique=True)
    company = models.CharField(max_length=300)
    roles_json = models.JSONField(
        default=list, blank=True,
        help_text="Role titles merged from the source job descriptions.",
    )
    process_summary = models.TextField(blank=True, default='')
    steps_json = models.JSONField(
        default=list, blank=True,
        help_text="List of step objects with name and optional duration/details.",
    )
    notable = models.TextField(blank=True, default='')
    source_files_json = models.JSONField(
        default=list, blank=True,
        help_text="Field-guide job-description filenames this row was merged from.",
    )
    status = models.CharField(max_length=50, blank=True, default='')
    required_level = models.IntegerField(
        default=LEVEL_BASIC,
        choices=VISIBILITY_CHOICES,
        help_text="Minimum tier level required to view the full process detail.",
    )

    class Meta:
        ordering = ['company']
        verbose_name_plural = 'interview companies'

    def __str__(self):
        return self.company

    def get_absolute_url(self):
        return f'/interview/companies/{self.slug}'

    @property
    def step_count(self):
        return len(self.steps_json)

    @property
    def roles_display(self):
        return ', '.join(self.roles_json)
