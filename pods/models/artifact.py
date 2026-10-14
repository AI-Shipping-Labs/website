"""Pod meeting notes and transcripts (issue #1922).

Plain text only: an uploaded ``.txt``, ``.md`` or ``.vtt`` file is read as
UTF-8 into ``body``, so nothing goes to file storage. Members add them after
a meeting; members and staff read them; the author or staff delete them
(hard delete). The rules live in ``pods.services.artifacts`` so the member
pages, Studio and the staff API share them.
"""

from django.conf import settings
from django.db import models

from pods.models.meeting import PodMeeting
from pods.models.pod import Pod

__all__ = [
    'ARTIFACT_KIND_CHOICES',
    'ARTIFACT_KIND_NOTES',
    'ARTIFACT_KIND_TRANSCRIPT',
    'ARTIFACT_SOURCE_CHOICES',
    'ARTIFACT_SOURCE_PASTED',
    'ARTIFACT_SOURCE_UPLOADED',
    'ARTIFACT_TITLE_MAX_LENGTH',
    'PodArtifact',
]

ARTIFACT_KIND_NOTES = 'notes'
ARTIFACT_KIND_TRANSCRIPT = 'transcript'
ARTIFACT_KIND_CHOICES = [
    (ARTIFACT_KIND_NOTES, 'Notes'),
    (ARTIFACT_KIND_TRANSCRIPT, 'Transcript'),
]

ARTIFACT_SOURCE_PASTED = 'pasted'
ARTIFACT_SOURCE_UPLOADED = 'uploaded'
ARTIFACT_SOURCE_CHOICES = [
    (ARTIFACT_SOURCE_PASTED, 'Pasted'),
    (ARTIFACT_SOURCE_UPLOADED, 'Uploaded'),
]

ARTIFACT_TITLE_MAX_LENGTH = 200


class PodArtifact(models.Model):
    """One set of meeting notes or one transcript of a pod."""

    pod = models.ForeignKey(Pod, on_delete=models.CASCADE, related_name='artifacts')
    meeting = models.ForeignKey(
        PodMeeting,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='artifacts',
        help_text='Optional. Only a meeting of the same pod.',
    )
    kind = models.CharField(max_length=20, choices=ARTIFACT_KIND_CHOICES, default=ARTIFACT_KIND_NOTES)
    title = models.CharField(max_length=ARTIFACT_TITLE_MAX_LENGTH)
    body = models.TextField()
    source = models.CharField(max_length=20, choices=ARTIFACT_SOURCE_CHOICES)
    original_filename = models.CharField(max_length=255, blank=True, default='')
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pod_artifacts',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Stored so list surfaces never load and UTF-8-encode whole transcript
    # bodies just to size them (perf lane 2026-10-14); save() keeps it exact.
    size_bytes = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['pod', 'created_at'], name='pod_artifact_pod_created_idx'),
        ]

    def __str__(self):
        return f'{self.get_kind_display()}: {self.title} ({self.pod})'

    def save(self, *args, **kwargs):
        self.size_bytes = len(self.body.encode('utf-8'))
        super().save(*args, **kwargs)
