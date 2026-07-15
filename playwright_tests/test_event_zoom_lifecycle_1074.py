"""Playwright API coverage for Zoom-backed event lifecycle sync (#1074)."""

import os
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _reset_event_state():
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_api_token(email):
    from accounts.models import Token, User

    user = User.objects.create_user(email=email, is_staff=True)
    token = Token.objects.create(user=user, name="zoom-lifecycle")
    connection.close()
    return token.key


def _create_zoom_event(slug):
    from events.models import Event

    start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(
        second=0,
        microsecond=0,
    )
    event = Event.objects.create(
        title=f"Zoom Lifecycle {slug}",
        slug=slug,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status="upcoming",
        timezone="Europe/Berlin",
        origin="studio",
        platform="zoom",
        zoom_meeting_id=f"zoom-{slug}",
        zoom_join_url=f"https://zoom.us/j/{slug}",
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestApiZoomLifecycle:
    def test_api_patch_reschedule_patches_existing_zoom_meeting(
        self, django_server, page,
    ):
        _reset_event_state()
        token = _create_api_token("api-zoom-reschedule-1074@test.com")
        event = _create_zoom_event("api-reschedule-1074")
        new_start = event.start_datetime + timedelta(days=2, hours=1)
        new_end = new_start + timedelta(hours=2)

        with patch("events.services.zoom_lifecycle.update_meeting") as update_zoom:
            response = page.request.patch(
                f"{django_server}/api/events/{event.slug}",
                headers={"Authorization": f"Token {token}"},
                data={
                    "start_datetime": new_start.isoformat(),
                    "end_datetime": new_end.isoformat(),
                },
            )

        assert response.status == 200
        assert "zoom_error" not in response.json()
        update_zoom.assert_called_once()

        from events.models import Event

        saved = Event.objects.get(pk=event.pk)
        assert saved.zoom_meeting_id == f"zoom-{event.slug}"
        assert saved.zoom_join_url == f"https://zoom.us/j/{event.slug}"
        connection.close()

    def test_api_patch_cancel_deletes_zoom_meeting_and_clears_fields(
        self, django_server, page,
    ):
        _reset_event_state()
        token = _create_api_token("api-zoom-cancel-1074@test.com")
        event = _create_zoom_event("api-cancel-1074")

        with patch("events.services.zoom_lifecycle.delete_meeting") as delete_zoom:
            response = page.request.patch(
                f"{django_server}/api/events/{event.slug}",
                headers={"Authorization": f"Token {token}"},
                data={"status": "cancelled"},
            )

        assert response.status == 200
        body = response.json()
        assert body["status"] == "cancelled"
        assert body["zoom_join_url"] == ""
        delete_zoom.assert_called_once()

        from events.models import Event

        saved = Event.objects.get(pk=event.pk)
        assert saved.zoom_meeting_id == ""
        assert saved.zoom_join_url == ""
        connection.close()

    def test_api_series_rename_patches_auto_titled_zoom_occurrences(
        self, django_server, page,
    ):
        _reset_event_state()
        token = _create_api_token("api-zoom-series-1074@test.com")

        from events.models import Event, EventSeries

        start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(
            second=0,
            microsecond=0,
        )
        series = EventSeries.objects.create(
            name="Zoom Lifecycle Series",
            slug="zoom-lifecycle-series-1074",
            start_time=time(17, 0),
            timezone="UTC",
        )
        occurrences = []
        for position in (1, 2):
            occurrence_start = start + timedelta(days=7 * position)
            occurrences.append(Event.objects.create(
                title=f"Zoom Lifecycle Series — Session {position}",
                slug=f"zoom-lifecycle-series-1074-{position}",
                start_datetime=occurrence_start,
                end_datetime=occurrence_start + timedelta(hours=1),
                status="upcoming",
                timezone="UTC",
                origin="studio",
                platform="zoom",
                event_series=series,
                series_position=position,
                title_is_auto=True,
                zoom_meeting_id=f"zoom-series-1074-{position}",
                zoom_join_url=f"https://zoom.us/j/series-1074-{position}",
            ))
        connection.close()

        with patch("events.services.zoom_lifecycle.update_meeting") as update_zoom:
            response = page.request.patch(
                f"{django_server}/api/event-series/{series.pk}",
                headers={"Authorization": f"Token {token}"},
                data={"name": "Renamed Zoom Lifecycle Series"},
            )

        assert response.status == 200
        assert update_zoom.call_count == 2
        assert {call.args[0].pk for call in update_zoom.call_args_list} == {
            occurrence.pk for occurrence in occurrences
        }
        connection.close()
