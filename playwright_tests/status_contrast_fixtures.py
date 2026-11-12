"""Tagged content fixtures for the status contrast browser journeys."""

import datetime

from django.db import connection
from django.utils import timezone

from content.models import Article, Course, Download, Project
from events.models import Event


def seed_tag_result_types(tag):
    """Create each public result type represented by the tag detail page."""
    today = datetime.date.today()
    Article.objects.create(title='Contrast article', slug='contrast-article-1279', date=today, tags=[tag], published=True)
    Project.objects.create(title='Contrast project', slug='contrast-project-tag-1279', date=today, tags=[tag], published=True)
    Course.objects.create(title='Contrast course', slug='contrast-course-tag-1279', status='published', tags=[tag])
    Download.objects.create(title='Contrast download', slug='contrast-download-1279', tags=[tag], published=True)
    Event.objects.create(
        title='Contrast event', slug='contrast-event-tag-1279', tags=[tag],
        start_datetime=timezone.now() + datetime.timedelta(days=2), status='upcoming',
    )
    connection.close()
