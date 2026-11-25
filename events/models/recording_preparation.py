"""Durable receipt and deadlines for the draft-only Hub preparation stage."""

from django.db import models


class RecordingPreparation(models.Model):
    event = models.OneToOneField(
        'events.Event', on_delete=models.CASCADE, related_name='recording_preparation',
    )
    recording_uuid = models.CharField(max_length=255, blank=True, default='')
    ready_at = models.DateTimeField()
    delivery_id = models.CharField(max_length=255, blank=True, default='')
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=30, default='pending')
    error_code = models.CharField(max_length=100, blank=True, default='')
    artifacts = models.JSONField(default=list, blank=True)
    alerted_at = models.DateTimeField(null=True, blank=True)
    start_alerted_at = models.DateTimeField(null=True, blank=True)
    finish_alerted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
