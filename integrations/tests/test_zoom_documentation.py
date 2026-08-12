"""Contract tests for the Zoom operator runbook added for issue #1352."""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
ZOOM_GUIDE = REPO_ROOT / '_docs' / 'integrations' / 'zoom.md'
CONFIGURATION_GUIDE = REPO_ROOT / '_docs' / 'configuration.md'
SETUP_GUIDE = REPO_ROOT / '_docs' / 'setup.md'

EXPECTED_GRANULAR_SCOPES = {
    'meeting:write:meeting:admin',
    'meeting:read:meeting:admin',
    'meeting:update:meeting:admin',
    'meeting:delete:meeting:admin',
    'cloud_recording:read:recording:admin',
    'cloud_recording:read:list_recording_files:admin',
}


class ZoomDocumentationContractTest(SimpleTestCase):
    """Keep least privilege and safe verification explicit in operator docs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.zoom_guide = ZOOM_GUIDE.read_text(encoding='utf-8')
        cls.configuration_guide = CONFIGURATION_GUIDE.read_text(encoding='utf-8')
        cls.setup_guide = SETUP_GUIDE.read_text(encoding='utf-8')

    def test_scope_matrix_contains_exactly_the_six_required_scopes(self):
        scope_section = self.zoom_guide.split(
            '### Exact least-privilege granular scopes',
            maxsplit=1,
        )[1].split('### Add, save, and activate the scopes', maxsplit=1)[0]
        documented_scopes = set(
            re.findall(
                r'`((?:meeting|cloud_recording):[^`]+)`',
                scope_section,
            )
        )

        self.assertEqual(documented_scopes, EXPECTED_GRANULAR_SCOPES)
        for marker in (
            'POST /v2/users/me/meetings',
            'GET /v2/meetings/{meeting_id}',
            'PATCH /v2/meetings/{meeting_id}',
            'DELETE /v2/meetings/{meeting_id}',
            '`recording.completed` event subscription',
            '`recording_files[].download_url`',
        ):
            self.assertIn(marker, scope_section)

    def test_custom_role_uses_s2s_specific_permission_and_path(self):
        prerequisite_section = self.zoom_guide.split(
            '### Account and role prerequisites',
            maxsplit=1,
        )[1].split(
            '### Exact least-privilege granular scopes',
            maxsplit=1,
        )[0]

        self.assertIn(
            '**User Management → Roles → Role Settings → Advanced features →\n'
            '  Server-to-Server OAuth app** View/Edit permission',
            prerequisite_section,
        )
        self.assertIn(
            'account permission corresponding to every requested admin scope',
            prerequisite_section,
        )
        self.assertIn('`RecordingContent:Read`', prerequisite_section)
        self.assertNotIn('Zoom for developers', prerequisite_section)

    def test_bootstrap_docs_point_to_the_authoritative_zoom_runbook(self):
        for guide in (self.configuration_guide, self.setup_guide):
            self.assertIn(
                'integrations/zoom.md#server-to-server-oauth-app-setup',
                guide,
            )

        for scope in EXPECTED_GRANULAR_SCOPES:
            self.assertIn(f'`{scope}`', self.configuration_guide)

    def test_runbook_covers_activation_cache_webhook_and_safe_smoke(self):
        for marker in (
            'Manage → Created Apps',
            'Scopes → Add Scopes',
            '**Activation**',
            'Features/Access → Event Subscriptions',
            '`ZOOM_WEBHOOK_SECRET_TOKEN` is an HMAC verification secret',
            'roughly 55',
            'all gunicorn/web processes',
            'every Django-Q worker',
            'wait up to one hour',
            'Disposable lifecycle and recording smoke checklist',
            'Never use a real event',
            'Do not paste raw CLI responses',
            '`RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD=false`',
            '`recording.completed` row is processed',
        ):
            self.assertIn(marker, self.zoom_guide)

    def test_smoke_commands_fail_closed_against_production(self):
        smoke_section = self.zoom_guide.split(
            '## Disposable lifecycle and recording smoke checklist',
            maxsplit=1,
        )[1].split('## Official Zoom references', maxsplit=1)[0]

        for marker in (
            'export SMOKE_EXPECTED_BASE_URL="https://dev.aishippinglabs.com"',
            'export ASL_BASE_URL="$SMOKE_EXPECTED_BASE_URL"',
            'Matching non-production staff API token:',
            'export ASL_API_TOKEN',
            'require_nonproduction_zoom_smoke_target()',
            '"https://aishippinglabs.com"|"https://www.aishippinglabs.com"',
            'resolved_base" != "$expected_base',
            'require_nonproduction_zoom_smoke_target &&\n'
            '  uv run asl events list --status draft --format json >/dev/null',
            'inside the same non-production\n'
            '   deployed application environment behind `ASL_BASE_URL`',
            'actual_base in production_urls or actual_base != expected_base',
            'any `manage.py shell` or `clear_token_cache()` step',
        ):
            self.assertIn(marker, smoke_section)

        production_rejection = smoke_section.index(
            '"https://aishippinglabs.com"|"https://www.aishippinglabs.com"'
        )
        first_api_request = smoke_section.index('uv run asl events list')
        self.assertLess(production_rejection, first_api_request)

        for command in (
            'uv run asl events create',
            'uv run asl events update',
            'uv run asl events sync-zoom',
            'uv run asl events get',
        ):
            positions = [
                match.start()
                for match in re.finditer(re.escape(command), smoke_section)
            ]
            self.assertTrue(positions, f'missing smoke command: {command}')
            for position in positions:
                guarded_prefix = smoke_section[max(0, position - 80):position]
                self.assertIn(
                    'require_nonproduction_zoom_smoke_target &&',
                    guarded_prefix,
                    f'unguarded smoke mutation: {command}',
                )
