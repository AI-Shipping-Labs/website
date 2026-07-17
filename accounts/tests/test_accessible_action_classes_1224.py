import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, tag

FOCUS_CLASSES = {
    'focus-visible:outline-none',
    'focus-visible:ring-2',
    'focus-visible:ring-accent',
    'focus-visible:ring-offset-2',
    'focus-visible:ring-offset-background',
}
REQUIRED_CLASSES = FOCUS_CLASSES | {'min-h-[44px]'}
TEMPLATES = Path(settings.BASE_DIR) / 'templates'


def _opening_tag(source, token):
    match = re.search(
        rf'<(?:a|button)\b[^>]*{re.escape(token)}[^>]*>',
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(f'No actionable opening tag contains {token!r}')
    return match.group(0)


def _classes(opening_tag):
    match = re.search(r'class="([^"]+)"', opening_tag, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f'Opening tag has no class attribute: {opening_tag}')
    return set(match.group(1).split())


@tag('visual_regression')
class AccessibleActionClassContractTest(SimpleTestCase):
    """Cross-surface class contract for the audited actions in issue #1224."""

    scoped_controls = {
        'accounts/includes/_login_form.html': ('id="login-submit"',),
        'accounts/includes/_register_form.html': ('id="register-submit"',),
        'accounts/includes/_password_reset_request_form.html': (
            'id="password-reset-request-submit"',
        ),
        'accounts/includes/_oauth_providers.html': (
            'data-testid="oauth-google-action"',
            'data-testid="oauth-github-action"',
            'data-testid="oauth-slack-action"',
        ),
        'content/projects_list.html': (
            'data-testid="project-difficulty-clear"',
            'data-testid="project-difficulty-{{ diff }}"',
        ),
        'content/peer_review/certificate.html': (
            'data-testid="certificate-pdf-link"',
        ),
        'content/about.html': ('data-testid="about-pricing-cta"',),
        'content/_gated_access_card.html': (
            'data-testid="{{ gated_cta_testid }}"',
        ),
        'events/event_series.html': (
            'data-testid="series-register-login-cta"',
            'data-testid="series-register-button"',
            'data-testid="series-cancel-button"',
        ),
        'voting/poll_detail.html': (
            'class="vote-btn',
            'data-testid="poll-proposal-submit"',
        ),
    }

    def test_every_scoped_control_has_full_focus_and_target_contract(self):
        for relative_path, tokens in self.scoped_controls.items():
            source = (TEMPLATES / relative_path).read_text()
            for token in tokens:
                with self.subTest(template=relative_path, control=token):
                    opening_tag = _opening_tag(source, token)
                    self.assertTrue(
                        REQUIRED_CLASSES.issubset(_classes(opening_tag)),
                        REQUIRED_CLASSES - _classes(opening_tag),
                    )

    def test_all_three_enabled_oauth_variants_render_with_contract(self):
        html = render_to_string(
            'accounts/includes/_oauth_providers.html',
            {
                'oauth_google_enabled': True,
                'oauth_github_enabled': True,
                'oauth_slack_enabled': True,
                'oauth_action': 'Sign up',
                'oauth_divider_text': 'sign up with',
                'next_url': '/projects?tag=python',
            },
        )

        for provider in ('google', 'github', 'slack'):
            with self.subTest(provider=provider):
                opening_tag = _opening_tag(
                    html, f'data-testid="oauth-{provider}-action"'
                )
                self.assertTrue(REQUIRED_CLASSES.issubset(_classes(opening_tag)))
                self.assertIn('next=/projects%3Ftag%3Dpython', opening_tag)

    def test_non_interactive_series_status_is_not_promoted_to_a_control(self):
        source = (TEMPLATES / 'events/event_series.html').read_text()
        match = re.search(
            r'{%\s*member_status_badge\b[^\n]*'
            r'testid="series-registered-state"[^\n]*%}',
            source,
        )
        self.assertIsNotNone(match)
        invocation = match.group(0)
        self.assertTrue(all(css_class not in invocation for css_class in FOCUS_CLASSES))
        self.assertNotIn('min-h-[44px]', invocation)
