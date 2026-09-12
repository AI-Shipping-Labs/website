"""Preview or enqueue recovery for incomplete Zoom transcript pipelines."""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from events.models import Event
from events.services.recording_transcript import (
    enqueue_recording_transcript_task,
    refresh_transcript_from_zoom,
)
from events.services.recording_upload import claim_and_enqueue_recording_upload
from integrations.services.zoom import ZoomAPIError


class Command(BaseCommand):
    help = (
        'Preview or enqueue transcript/archive recovery for ended Zoom events. '
        'Dry-run by default; pass --commit to contact Zoom and enqueue work.'
    )

    def add_arguments(self, parser):
        selectors = parser.add_mutually_exclusive_group(required=True)
        selectors.add_argument('--slug', help='Process one event by slug.')
        selectors.add_argument(
            '--all-missing',
            action='store_true',
            help='Process every eligible event missing text or its VTT archive.',
        )
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Contact Zoom and enqueue recovery. Default is dry-run.',
        )

    def handle(self, *args, **options):
        events = self._select_events(options['slug'], options['all_missing'])
        commit = options['commit']
        mode = 'COMMIT' if commit else 'DRY-RUN'
        self.stdout.write(
            self.style.NOTICE(f'[{mode}] selected={len(events)}')
        )

        counts = {
            'would_process': 0,
            'complete': 0,
            'recording_queued': 0,
            'transcript_queued': 0,
            'unavailable': 0,
            'failed': 0,
        }
        for event in events:
            label = f'event={event.pk} slug={event.slug}'
            if event.transcript_text and event.transcript_s3_url:
                counts['complete'] += 1
                self.stdout.write(f'  {label} outcome=complete')
                continue
            if not commit:
                counts['would_process'] += 1
                self.stdout.write(f'  {label} outcome=would_process')
                continue

            try:
                refresh_result = refresh_transcript_from_zoom(event)
            except ZoomAPIError:
                counts['failed'] += 1
                self.stderr.write(
                    self.style.ERROR(f'  {label} outcome=zoom_refresh_failed')
                )
                continue
            except Exception:
                counts['failed'] += 1
                self.stderr.write(
                    self.style.ERROR(f'  {label} outcome=refresh_failed')
                )
                continue

            refresh_upload_queued = bool(
                isinstance(refresh_result, dict)
                and refresh_result.get('upload_queued')
            )
            if refresh_upload_queued:
                counts['recording_queued'] += 1

            event.refresh_from_db()
            recording_result = 'uploaded'
            if not event.recording_s3_url:
                try:
                    _event, recording_result = claim_and_enqueue_recording_upload(
                        event.pk,
                        source='Transcript backfill command',
                    )
                except Exception:
                    counts['failed'] += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f'  {label} outcome=recording_enqueue_failed'
                        )
                    )
                    continue
                if recording_result == 'queued' and not refresh_upload_queued:
                    counts['recording_queued'] += 1

            event.refresh_from_db()
            if (
                event.transcript_unavailable_at is not None
                and not event.transcript_text
            ):
                counts['unavailable'] += 1
                self.stdout.write(
                    f'  {label} outcome=unavailable '
                    f'recording={recording_result}'
                )
                continue

            try:
                enqueue_recording_transcript_task(
                    event,
                    source='Transcript backfill command',
                )
            except Exception:
                counts['failed'] += 1
                self.stderr.write(
                    self.style.ERROR(
                        f'  {label} outcome=transcript_enqueue_failed'
                    )
                )
                continue
            counts['transcript_queued'] += 1
            self.stdout.write(
                f'  {label} outcome=queued recording={recording_result}'
            )

        summary = ' '.join(
            [f'selected={len(events)}']
            + [f'{key}={value}' for key, value in counts.items()]
        )
        self.stdout.write(f'Done. {summary}')
        if counts['failed']:
            raise CommandError(
                f'Transcript recovery had {counts["failed"]} failed event(s).'
            )

    def _select_events(self, slug, all_missing):
        if slug:
            event = Event.objects.filter(slug=slug).first()
            if event is None:
                raise CommandError(f'No event with slug={slug}.')
            self._validate_explicit_event(event)
            return [event]

        if not all_missing:
            # argparse enforces this, but keep the method fail-closed when
            # invoked directly in tests or by future callers.
            raise CommandError('Pass exactly one of --slug or --all-missing.')

        candidates = (
            Event.objects.filter(platform='zoom')
            .exclude(status='cancelled')
            .exclude(zoom_meeting_id='')
            .filter(Q(transcript_text='') | Q(transcript_s3_url=''))
            .order_by('start_datetime', 'pk')
        )
        return [event for event in candidates if event.is_past]

    def _validate_explicit_event(self, event):
        if event.platform != 'zoom' or not event.zoom_meeting_id:
            raise CommandError('The selected event is not a Zoom event.')
        if not event.is_past:
            raise CommandError('The selected event has not ended.')
