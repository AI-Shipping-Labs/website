"""Offline Relay stand-in for recap-ready tests (no SES, no network)."""

from unittest.mock import patch

SUBMIT = 'events.services.event_recap_notification.submit_event_notice_campaign'
STATS = 'events.services.relay_event_notice.get_native_campaign_stats'


class RecapRelay:
    """Records campaign submits; ``fail`` makes the next submits raise."""

    def __init__(self):
        self.campaigns = []
        self.fail = False

    def submit(self, **kwargs):
        if self.fail:
            raise RuntimeError('relay unavailable')
        self.campaigns.append(kwargs)
        return {'status': 'queued'}

    def stats(self, external_key, **_kwargs):
        for campaign in self.campaigns:
            if campaign['external_key'] == external_key:
                count = len(campaign['recipients'])
                return {
                    'recipient_count': count,
                    'sent_count': count,
                    'delivered_count': max(count - 1, 0),
                    'pending_count': 0,
                }
        return None

    @property
    def emails(self):
        return sorted(
            member['email']
            for campaign in self.campaigns
            for member in campaign['recipients']
        )

    def contexts(self):
        """Per-recipient template context keyed by email."""
        return {
            member['email']: member['metadata']['relay_campaign_context']
            for campaign in self.campaigns
            for member in campaign['recipients']
        }


def start_recap_relay(test_case):
    relay = RecapRelay()
    for target, side_effect in ((SUBMIT, relay.submit), (STATS, relay.stats)):
        patcher = patch(target, side_effect=side_effect)
        patcher.start()
        test_case.addCleanup(patcher.stop)
    return relay
