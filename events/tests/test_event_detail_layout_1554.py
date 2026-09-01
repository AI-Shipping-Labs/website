"""Rendered presentation contracts for the event detail reader (issue #1554)."""

import re
from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone

from events.models import Event


@pytest.mark.visual_regression
class EventDetailLayoutContractTest(TestCase):
    """Keep the shared detail page inside its documented Reader layout."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title="A long event title that must wrap inside the reader column",
            slug="reader-layout-event",
            description="Readable supporting event description.",
            start_datetime=timezone.now() - timedelta(hours=3),
            end_datetime=timezone.now() - timedelta(hours=1),
            status="completed",
            location="A location with enough text to wrap on a narrow screen",
            recap_html="<p>Published session notes.</p>",
        )

    def test_reader_shell_and_content_prevent_horizontal_spill(self):
        response = self.client.get(self.event.get_absolute_url())

        self.assertContains(
            response,
            'class="py-8 sm:py-16 lg:py-24 overflow-x-hidden"',
        )
        self.assertContains(
            response,
            'class="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8"',
        )
        self.assertContains(
            response,
            'class="prose min-w-0 max-w-full" data-testid="event-description"',
            count=1,
        )

    def test_header_and_recap_keep_compact_reader_rhythm(self):
        response = self.client.get(self.event.get_absolute_url())
        html = response.content.decode()

        self.assertContains(
            response,
            'class="[text-wrap:balance] break-words text-3xl font-semibold '
            'tracking-tight sm:text-4xl"',
        )
        self.assertContains(
            response,
            'class="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2 '
            'text-sm text-muted-foreground"',
        )
        recap = re.search(
            r'<section class="([^"]+)"[^>]+data-testid="event-recap-cta"',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(recap)
        recap_classes = recap.group(1).split()
        for expected in (
            "rounded-lg",
            "border",
            "border-border",
            "p-6",
            "mt-12",
            "mb-12",
            "bg-card",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, recap_classes)
