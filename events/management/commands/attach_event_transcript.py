"""Attach an operator-supplied transcript VTT to an event.

Recovery path for meetings whose Zoom transcript never existed: transcribe
the S3 recording locally, then attach the resulting VTT. The command parses
the VTT, archives the exact bytes plus the parsed plain text to the private
recordings bucket (the same .vtt + .txt pair the automatic pipeline stores
on a video upload), stores transcript_text / transcript_s3_url, and clears
the unavailable marker.

Dry-run by default; pass --commit to write.
"""

from django.core.management.base import BaseCommand, CommandError

from events.models import Event
from events.services.recording_transcript import attach_transcript_vtt
from jobs.tasks.recording_transcript import (
    TranscriptArchiveError,
    parse_vtt_to_text,
)
from jobs.tasks.recordings_s3 import (
    build_transcript_s3_key,
    build_transcript_text_s3_key,
)


class Command(BaseCommand):
    help = (
        'Attach a local transcript VTT file to an event and archive it '
        'to S3. Dry-run by default; pass --commit to write.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True, help='Event slug.')
        parser.add_argument(
            '--vtt',
            required=True,
            help='Path to the WebVTT file to attach.',
        )
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Archive to S3 and store on the event. Default is dry-run.',
        )

    def handle(self, *args, **options):
        event = Event.objects.filter(slug=options['slug']).first()
        if event is None:
            raise CommandError(f"No event with slug={options['slug']}.")
        try:
            with open(options['vtt'], 'rb') as handle:
                raw_vtt = handle.read()
        except OSError as exc:
            raise CommandError(f'Cannot read VTT file: {exc}') from exc

        text = parse_vtt_to_text(raw_vtt)
        if not text:
            raise CommandError('The supplied VTT parses to empty text.')

        mode = 'COMMIT' if options['commit'] else 'DRY-RUN'
        self.stdout.write(
            self.style.NOTICE(
                f'[{mode}] slug={event.slug} '
                f'characters={len(text)} '
                f'vtt_key={build_transcript_s3_key(event)} '
                f'txt_key={build_transcript_text_s3_key(event)}',
            )
        )
        if not options['commit']:
            return

        try:
            attached = attach_transcript_vtt(event, raw_vtt)
        except TranscriptArchiveError as exc:
            raise CommandError(f'Transcript archival failed: {exc}') from exc
        self.stdout.write(
            f"Done. characters={attached['characters']} "
            f"transcript_s3_url={attached['transcript_s3_url']} "
            f"transcript_txt_s3_url={attached['transcript_txt_s3_url']}",
        )
