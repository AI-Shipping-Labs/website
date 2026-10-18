"""OpenAPI examples for study-group endpoints."""

from pods.services import meetings as mtg

_RESULTS_EXAMPLE = {
    "added": ["anna@example.com", "raj@example.com"],
    "not_enrolled": ["someone@example.com"],
    "unknown_user": [],
    "already_member": [],
}

_POD_SUMMARY_EXAMPLE = {
    "id": 7,
    "course_slug": "ai-buildcamp",
    "cohort_key": "4",
    "cohort_name": "Cohort 4",
    "sprint_slug": None,
    "sprint_name": None,
    "name": "Agents pod",
    "purpose": "Ship an agent demo",
    "scheduling_note": "Meet on Tuesdays when possible.",
    "internal_notes": "Imported formation history visible to staff only.",
    "status": "open",
    "member_count": 2,
    "meeting_count": 3,
    "meeting_minutes": 60,
    "owner_email": "anna@example.com",
    "source": "api",
    "slack_channel_url": "",
    "created_at": "2026-10-01T09:00:00+00:00",
}

_MEETING_EXAMPLE = {
    "id": 31,
    "number": 2,
    "starts_at": "2026-10-13T16:00:00+00:00",
    "ends_at": "2026-10-13T17:00:00+00:00",
    "duration_minutes": 60,
    "timezone": "Europe/Berlin",
    "status": "scheduled",
    "expired": False,
    "series_id": "6f1c2d4e-8a0b-4c3d-9e5f-1a2b3c4d5e6f",
    "created_via": "member",
    "proposed_by": "anna@example.com",
    "moved_by": None,
    "moved_at": None,
    "previous_starts_at": None,
    "reminder_sent_at": None,
    "responses": [{"email": "mike@example.com", "response": "cant_make_it"}],
    "waiting_on": [],
    "stuck": False,
    "confirmable": None,
    "stuck_alerted_at": None,
}

_POD_DETAIL_EXAMPLE = {
    **_POD_SUMMARY_EXAMPLE,
    "members": [
        {
            "email": "anna@example.com",
            "first_name": "Anna",
            "last_name": "Klein",
            "timezone": "Europe/Berlin",
            "joined_at": "2026-10-01T09:00:00+00:00",
            "source": "api",
        }
    ],
    "meeting_url": "https://meet.google.com/abc-defg-hij",
    "message_count": 12,
    "last_message_at": "2026-10-05T18:30:00+00:00",
    "artifact_count": 2,
    "meetings": [_MEETING_EXAMPLE],
    "suggested_slots": [
        {
            "start": "2026-10-06T16:00:00+00:00",
            "end": "2026-10-06T17:00:00+00:00",
            "available": ["anna@example.com", "raj@example.com"],
            "if_needed": [],
            "missing": [],
            "local_starts": [
                {"email": "anna@example.com", "timezone": "Europe/Berlin", "local_start": "2026-10-06T18:00:00+02:00"},
            ],
        }
    ],
}

_REQUEST_EXAMPLE = {
    "id": 12,
    "email": "mike@example.com",
    "status": "pending",
    "message": "I am building a RAG eval harness",
    "created_at": "2026-10-02T10:00:00+00:00",
    "decided_at": None,
    "decided_by": None,
    "stale_alerted_at": None,
}

_ROSTER_EXAMPLE = {
    "email": "anna@example.com",
    "first_name": "Anna",
    "last_name": "Klein",
    "tier": "main",
    "tags": ["maven", "ai-buildcamp", "ai-buildcamp-4"],
    "preferred_timezone": "Europe/Berlin",
    "slack_member": True,
    "has_slack_user_id": True,
    "enrolled_at": "2026-09-10T08:00:00+00:00",
    "crm": {
        "status": "active",
        "persona": "Builder",
        "summary": "Ships RAG apps at work.",
        "next_steps": "Pair with someone in Asia.",
    },
    "availability": {
        "timezone": "Europe/Berlin",
        "updated_at": "2026-09-20T12:00:00+00:00",
        "windows": [{"weekday": 1, "start": "18:00", "end": "21:00", "preference": "preferred"}],
    },
    "pod_ids": [7],
}

_ERR_UNKNOWN_POD = {"error": "Pod not found", "code": "unknown_pod"}
_ERR_UNKNOWN_SPRINT = {"error": "Sprint not found", "code": "unknown_sprint"}
_ERR_LIMIT = {"error": mtg.msg_limit(4), "code": mtg.CODE_LIMIT}
