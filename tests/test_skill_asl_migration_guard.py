"""Static guard: prod-API operator skills must call the ``asl`` CLI, not curl.

Issue #1132 migrated the two remaining prod-API operator skills
(``ai-shipping-labs-plan-event-ops`` and ``ai-shipping-labs-plan-from-onboarding``)
off hand-rolled ``curl`` + ``.env`` token extraction and onto the ``asl`` CLI
(``uv run asl <group> <command>``), which resolves the staff token, base URL,
and output format.

This is a fail-closed content guard: if a future edit reintroduces an inline
``curl`` call to the production API, an ``Authorization: Token`` header, or the
``grep ... .env`` token-extraction incantation, these assertions fail. It does
not run any command — it only reads the shipped ``SKILL.md`` files.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase, tag

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _REPO_ROOT / '.claude' / 'skills'

# The two skills migrated by #1132. These must be curl-free for the prod API.
_MIGRATED_SKILLS = (
    'ai-shipping-labs-plan-event-ops',
    'ai-shipping-labs-plan-from-onboarding',
)

# A curl invocation that targets the token-authenticated production API.
# Applied after line-continuations are collapsed so a multi-line
# ``curl ... \`` command (path on the next line) is still caught.
_CURL_TO_API = re.compile(r'curl\b[^\n]*\/(?:member-)?api\b')


def _collapse_continuations(text):
    """Join shell line-continuations (``\\`` + newline) into one line."""
    return re.sub(r'\\\n\s*', ' ', text)
# The Authorization: Token header used with the old raw curl calls.
_AUTH_TOKEN_HEADER = re.compile(r'Authorization:\s*Token')
# The hand-rolled .env token-extraction incantation.
_ENV_TOKEN_GREP = re.compile(
    r"grep[^\n]*API_SHIPPING_LABS_API_TOKEN[^\n]*\.env"
)


def _skill_body(name):
    path = _SKILLS_DIR / name / 'SKILL.md'
    return path, path.read_text(encoding='utf-8')


@tag('core')
class SkillAslMigrationGuardTest(SimpleTestCase):
    """The migrated prod-API skills must drive the API through ``asl``."""

    def test_migrated_skills_exist(self):
        # Fail loudly if a skill is renamed/removed so the guard cannot be
        # silently disabled by the file disappearing.
        for name in _MIGRATED_SKILLS:
            path, body = _skill_body(name)
            self.assertTrue(path.exists(), f'missing skill file: {path}')
            self.assertTrue(body.strip(), f'empty skill file: {path}')

    def test_no_inline_curl_to_production_api(self):
        for name in _MIGRATED_SKILLS:
            path, body = _skill_body(name)
            match = _CURL_TO_API.search(_collapse_continuations(body))
            self.assertIsNone(
                match,
                f'{path} reintroduced an inline curl to the production API: '
                f'{match.group(0) if match else ""!r}',
            )

    def test_no_authorization_token_header(self):
        for name in _MIGRATED_SKILLS:
            path, body = _skill_body(name)
            self.assertIsNone(
                _AUTH_TOKEN_HEADER.search(body),
                f'{path} reintroduced an "Authorization: Token" curl header',
            )

    def test_no_env_token_grep_incantation(self):
        for name in _MIGRATED_SKILLS:
            path, body = _skill_body(name)
            self.assertIsNone(
                _ENV_TOKEN_GREP.search(body),
                f'{path} reintroduced the .env token-grep incantation',
            )

    def test_skills_invoke_asl_cli(self):
        # Positive assertion: the migration left real asl invocations behind,
        # so the guard cannot pass on an empty/stubbed file.
        for name in _MIGRATED_SKILLS:
            path, body = _skill_body(name)
            self.assertIn(
                'uv run asl ',
                body,
                f'{path} no longer invokes the asl CLI',
            )
