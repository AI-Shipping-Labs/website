"""``asl announcements`` -- cohort targeting, preview, and publish guards."""

from __future__ import annotations

import json

import pytest
from asl_cli.cli import cli
from asl_cli.commands import announcements as module
from click.testing import CliRunner

COHORT4 = {"id": 1, "external_key": "4", "name": "Cohort 4"}
COHORT3 = {"id": 6, "external_key": "3", "name": "Cohort 3"}


def preview(cohort=COHORT4, stored=1, count=54, blocked=None, cohorts=5, status="draft"):
    return {
        "announcement_id": 4,
        "course": "ai-buildcamp",
        "status": status,
        "cohort": cohort,
        "stored_cohort_id": stored,
        "recipient_count": count,
        "course_active_enrollment_count": 167,
        "course_cohort_count": cohorts,
        "email_queued_at": None,
        "will_email": blocked is None,
        "blocked_reason": blocked,
    }


class FakeClient:
    def __init__(self, preview_payload=None):
        self.preview_payload = preview_payload or preview()
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if path.endswith("/recipients"):
            return self.preview_payload
        return {"announcements": []}

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return {**kwargs["json_body"], "id": 9, "cohort": COHORT4}

    def patch(self, path, **kwargs):
        self.calls.append(("PATCH", path, kwargs))
        return {"id": 4, "status": "published", "cohort": COHORT4}

    @property
    def writes(self):
        return [call for call in self.calls if call[0] in ("POST", "PATCH")]


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(module, "get_client", lambda: client)
    return client


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_create_sends_cohort_key_and_always_drafts(fake):
    result = run("announcements", "create", "ai-buildcamp", "--cohort", "4",
                 "--title", "Feedback", "--body", "Please fill the form.")
    assert result.exit_code == 0, result.output
    assert fake.writes == [("POST", "/api/courses/ai-buildcamp/announcements", {
        "json_body": {"title": "Feedback", "body": "Please fill the form.",
                      "status": "draft", "cohort": "4"},
    })]


def test_create_accepts_cohort_id(fake):
    result = run("announcements", "create", "ai-buildcamp", "--cohort-id", "1",
                 "--title", "T", "--body", "B")
    assert result.exit_code == 0, result.output
    assert fake.writes[0][2]["json_body"]["cohort_id"] == 1


def test_create_without_cohort_is_refused(fake):
    result = run("announcements", "create", "ai-buildcamp", "--title", "T", "--body", "B")
    assert result.exit_code == 2
    assert "cohort is required" in result.output
    assert fake.calls == []


def test_update_sends_cohort(fake):
    result = run("announcements", "update", "ai-buildcamp", "5", "--cohort", "4")
    assert result.exit_code == 0, result.output
    assert fake.writes == [("PATCH", "/api/courses/ai-buildcamp/announcements/5",
                            {"json_body": {"cohort": "4"}})]


def test_preview_is_read_only_and_prints_count_and_cohort(fake):
    result = run("announcement", "preview", "ai-buildcamp", "4")
    assert result.exit_code == 0, result.output
    assert fake.writes == []
    assert fake.calls == [("GET", "/api/courses/ai-buildcamp/announcements/4/recipients",
                           {"params": None})]
    assert "Cohort 4 (key 4, id 1): 54 recipient(s)" in result.output


def test_preview_cohort_override_is_a_query_param(fake):
    result = run("announcements", "preview", "ai-buildcamp", "4", "--cohort", "3")
    assert result.exit_code == 0, result.output
    assert fake.calls[0][2] == {"params": {"cohort": "3"}}


def test_publish_without_yes_prints_count_and_sends_nothing(fake):
    result = run("announcements", "publish", "ai-buildcamp", "4", "--cohort", "4")
    assert result.exit_code == 1
    assert "54 recipient(s)" in result.output
    assert "Re-run with --yes" in result.output
    assert fake.writes == []


def test_publish_dry_run_sends_nothing(fake):
    result = run("announcements", "publish", "ai-buildcamp", "4", "--cohort", "4",
                 "--dry-run", "--yes")
    assert result.exit_code == 0, result.output
    assert "nothing sent" in result.output
    assert fake.writes == []


def test_publish_without_cohort_on_multi_cohort_course_is_refused(fake):
    result = run("announcements", "publish", "ai-buildcamp", "4", "--yes")
    assert result.exit_code == 1
    assert "Refusing to publish: ai-buildcamp has 5 cohorts" in result.output
    assert "--cohort 4" in result.output
    assert fake.writes == []


def test_publish_with_no_cohort_anywhere_is_refused(monkeypatch):
    client = FakeClient(preview(cohort=None, stored=None, count=0,
                                blocked="no_cohort", cohorts=1))
    monkeypatch.setattr(module, "get_client", lambda: client)
    result = run("announcements", "publish", "ai-buildcamp", "4", "--yes")
    assert result.exit_code == 1
    assert "has no cohort" in result.output
    assert client.writes == []


def test_publish_cohort_mismatch_with_stored_draft_is_refused(monkeypatch):
    client = FakeClient(preview(cohort=COHORT3, stored=1, count=48))
    monkeypatch.setattr(module, "get_client", lambda: client)
    result = run("announcements", "publish", "ai-buildcamp", "4", "--cohort", "3", "--yes")
    assert result.exit_code == 1
    assert "scoped to cohort id 1" in result.output
    assert client.writes == []


def test_publish_already_emailed_is_refused(monkeypatch):
    client = FakeClient(preview(blocked="already_emailed", status="published"))
    monkeypatch.setattr(module, "get_client", lambda: client)
    result = run("announcements", "publish", "ai-buildcamp", "4", "--cohort", "4", "--yes")
    assert result.exit_code == 1
    assert "never sent twice" in result.output
    assert client.writes == []


def test_publish_with_yes_sends_previewed_count_as_guard(fake):
    result = run("announcements", "publish", "ai-buildcamp", "4", "--cohort", "4", "--yes")
    assert result.exit_code == 0, result.output
    assert fake.writes == [("PATCH", "/api/courses/ai-buildcamp/announcements/4", {
        "json_body": {"status": "published", "expected_recipient_count": 54},
    })]
    assert "emailing 54 learner(s) of Cohort 4" in result.output


def test_publish_sets_cohort_on_unscoped_draft(monkeypatch):
    client = FakeClient(preview(stored=None))
    monkeypatch.setattr(module, "get_client", lambda: client)
    result = run("announcements", "publish", "ai-buildcamp", "4", "--cohort", "4", "--yes")
    assert result.exit_code == 0, result.output
    assert client.writes[0][2]["json_body"] == {
        "status": "published", "expected_recipient_count": 54, "cohort_id": 1,
    }


def test_list_passes_filters(fake):
    result = run("announcements", "list", "ai-buildcamp", "--status", "draft", "--cohort", "4")
    assert result.exit_code == 0, result.output
    assert fake.calls == [("GET", "/api/courses/ai-buildcamp/announcements",
                           {"params": {"status": "draft", "cohort": "4"}})]
    assert json.loads(result.output) == {"announcements": []}
