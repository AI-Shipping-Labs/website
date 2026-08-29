from asl_cli.cli import cli
from asl_cli.commands import events as events_module
from click.testing import CliRunner


class RecordingClient:
    def __init__(self, *, post_status='sent', mismatch=False):
        self.calls = []
        self.post_status = post_status
        self.mismatch = mismatch

    def get(self, path, **kwargs):
        self.calls.append(('GET', path, None))
        if path == '/api/events/id/49':
            return {'id': 49, 'slug': 'remote-env', 'title': 'Remote env'}
        return {
            'event_id': 49,
            'event_slug': 'wrong' if self.mismatch else 'remote-env',
            'event_title': 'Remote env',
            'guest_email': 'guest@example.com',
            'registration_id': 7,
            'registration_status': 'registered',
            'email_status': (
                'failed_retryable' if self.post_status == 'failed_retryable'
                else 'sent'
            ),
        }

    def post(self, path, *, json_body=None):
        self.calls.append(('POST', path, json_body))
        return {
            'event_id': 49,
            'event_slug': 'remote-env',
            'event_title': 'Remote env',
            'guest_email': 'guest@example.com',
            'registration_id': None if json_body['dry_run'] else 7,
            'registration_status': (
                'would_register' if json_body['dry_run'] else 'registered'
            ),
            'email_status': (
                'would_send' if json_body['dry_run'] else self.post_status
            ),
        }


def invoke(monkeypatch, client, *args):
    monkeypatch.setattr(events_module, 'get_client', lambda: client)
    return CliRunner().invoke(cli, ['events', 'invite-guest', *args])


def test_live_command_uses_get_post_get_order_and_bounded_summary(monkeypatch):
    client = RecordingClient()
    result = invoke(
        monkeypatch, client, '49', '--email', 'guest@example.com', '--format', 'raw',
    )
    assert result.exit_code == 0, result.output
    assert client.calls == [
        ('GET', '/api/events/id/49', None),
        ('POST', '/api/events/id/49/guest-invitations', {
            'email': 'guest@example.com', 'dry_run': False,
        }),
        ('GET', '/api/events/id/49/guest-invitations/7', None),
    ]
    assert '"verified": true' in result.output
    assert 'attempt_count' not in result.output


def test_dry_run_stops_after_validation_post(monkeypatch):
    client = RecordingClient()
    result = invoke(
        monkeypatch, client, '49', '--email', 'guest@example.com', '--dry-run',
    )
    assert result.exit_code == 0, result.output
    assert [call[0] for call in client.calls] == ['GET', 'POST']
    assert '"registration_status": "would_register"' in result.output


def test_retryable_failure_renders_summary_and_exits_nonzero(monkeypatch):
    client = RecordingClient(post_status='failed_retryable')
    result = invoke(monkeypatch, client, '49', '--email', 'guest@example.com')
    assert result.exit_code == 1
    assert '"email_status": "failed_retryable"' in result.output
    assert '"verified": true' in result.output


def test_read_after_write_mismatch_exits_nonzero_without_success_claim(monkeypatch):
    client = RecordingClient(mismatch=True)
    result = invoke(monkeypatch, client, '49', '--email', 'guest@example.com')
    assert result.exit_code != 0
    assert 'read-after-write verification failed' in result.output
    assert '"verified": true' not in result.output


def test_numeric_event_id_and_required_email_are_validated_before_requests(monkeypatch):
    client = RecordingClient()
    missing = invoke(monkeypatch, client, '49')
    slug = invoke(monkeypatch, client, 'remote-env', '--email', 'guest@example.com')
    assert missing.exit_code != 0
    assert slug.exit_code != 0
    assert client.calls == []


def test_help_names_every_live_status_and_readback_verification_field():
    result = CliRunner().invoke(cli, ['events', 'invite-guest', '--help'])

    assert result.exit_code == 0, result.output
    for term in (
        'registered',
        'sent',
        'already_registered',
        'already_sent',
        'failed_retryable',
        'verified',
    ):
        assert term in result.output
