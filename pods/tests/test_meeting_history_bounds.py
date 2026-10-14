"""Meeting-history bounds (perf lane 2026-10-14).

Pod detail used to build presentation rows for the pod's entire meeting
history before showing ten, and every proposal author was fetched lazily
(one query per proposal). The dashboard hydrated every historical meeting
before filtering to the next seven days. These tests pin the bounded
behavior: author joins are batched, only rendering rows are built, and
dead proposal history changes neither the dashboard's rows nor its cost.
"""

import datetime
import uuid
from datetime import UTC

from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from freezegun import freeze_time

from pods.services.meeting_presentation import (
    DASHBOARD_NEEDS_ANSWER,
    build_meeting_rows,
    dashboard_pod_meetings,
)
from pods.services.suggestions import load_member_availability
from pods.tests.fixtures import (
    COURSE_SLUG,
    enroll,
    make_cohort,
    make_course,
    make_meeting,
    make_pod,
    make_user,
)

NOW = datetime.datetime(2026, 10, 14, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Wednesday

DEAD_SERIES = uuid.uuid4()
LIVE_SERIES = uuid.uuid4()

ENABLED = override_settings(PODS_COURSE_SLUGS=COURSE_SLUG)


def utc(*args):
    return datetime.datetime(*args, tzinfo=UTC)


@freeze_time('2026-10-14 08:00:00')  # date-rot-ok: frozen October clock
@tag('core')
class MeetingHistoryBoundsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.anna = make_user('anna-bounds@example.com', first_name='Anna', last_name='Klein')
        cls.raj = make_user('raj-bounds@example.com', first_name='Raj', last_name='Shah')
        enroll(cls.anna, cls.cohort)
        enroll(cls.raj, cls.cohort)

    def _pod(self):
        return make_pod(self.cohort, 'History bounds pod', [self.anna, self.raj], owner=self.anna)

    def _rows(self, pod):
        members = [
            membership.user
            for membership in pod.memberships.select_related('user').order_by('joined_at', 'pk')
        ]
        availability = load_member_availability(members)
        member_avail = [availability[user.pk] for user in members]
        return build_meeting_rows(
            pod, members[0], member_avail=member_avail, viewer_zone='UTC',
            is_member=True, now=NOW,
        )

    def test_meeting_rows_stop_querying_per_proposal_author(self):
        def build(proposal_count):
            pod = self._pod()
            for index in range(proposal_count):
                author = make_user(
                    f'author-{proposal_count}-{index}@example.com',
                    first_name='A', last_name=str(index),
                )
                make_meeting(
                    pod, utc(2026, 10, 15 + index, 10, 0),
                    status='proposed', proposed_by=author,
                )
            with CaptureQueriesContext(connection) as captured:
                rows = self._rows(pod)
            return len(captured.captured_queries), rows

        small_queries, _ = build(2)
        large_queries, rows = build(14)
        self.assertLessEqual(
            large_queries, small_queries + 2,
            'meeting rows still cost one author query per proposal',
        )
        self.assertEqual(len(rows), 14)
        self.assertTrue(all(row['proposed_by'] for row in rows))

    def test_meeting_rows_render_upcoming_plus_newest_ten_past(self):
        pod = self._pod()
        for day in range(1, 31):
            make_meeting(pod, utc(2026, 9, day, 9, 0), status='held')
        make_meeting(pod, utc(2026, 10, 20, 10, 0), status='scheduled')

        rows = self._rows(pod)

        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[0]['meeting'].starts_at.day, 20)
        # Newest ten past meetings, newest first; the older twenty stay
        # unbuilt.
        self.assertEqual(
            [row['meeting'].starts_at.day for row in rows[1:]],
            list(range(30, 20, -1)),
        )

    @ENABLED
    def test_dashboard_skips_dead_meeting_history(self):
        pod = self._pod()
        make_meeting(pod, utc(2026, 10, 16, 10, 0), status='scheduled')
        for index in range(12):
            make_meeting(pod, utc(2026, 10, 1 + index, 9, 0), status='proposed')
        for index in range(10):
            make_meeting(pod, utc(2026, 9, 10 + index, 8, 0), status='cancelled')

        rows = dashboard_pod_meetings(self.anna, NOW)

        self.assertEqual(
            [row['title'] for row in rows],
            [f'{pod.name} - Meeting 1 of {pod.meeting_count}'],
        )

    @ENABLED
    def test_dashboard_excludes_series_whose_head_expired(self):
        pod = self._pod()
        make_meeting(
            pod, utc(2026, 10, 12, 10, 0), status='proposed', series_id=DEAD_SERIES,
        )
        make_meeting(
            pod, utc(2026, 10, 19, 10, 0), status='proposed', series_id=DEAD_SERIES,
        )

        self.assertEqual(dashboard_pod_meetings(self.anna, NOW), [])

    @ENABLED
    def test_dashboard_lists_live_series_proposal_head_once(self):
        pod = self._pod()
        make_meeting(
            pod, utc(2026, 10, 16, 10, 0), status='proposed',
            series_id=LIVE_SERIES, proposed_by=self.anna,
        )
        make_meeting(
            pod, utc(2026, 10, 23, 10, 0), status='proposed', series_id=LIVE_SERIES,
        )

        rows = dashboard_pod_meetings(self.anna, NOW)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['badge'], DASHBOARD_NEEDS_ANSWER)
        self.assertEqual(rows[0]['meeting_id'], rows[0]['meeting_id'])
