"""Issue #1591: nameless members must be greeted ``Hi there,``, never
``Hi x.arrieta,``.

The fallback is resolved in ``EmailService._render_template_with_footer``,
not in the ``.md`` files. That is deliberate: operators can override any
template from Studio and those overrides live in the database, so a
``|default:"there"`` sitting in the shipped files would still mail the email
handle for every override row that reads ``Hi {{ user_name }},``.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.utils.display import GREETING_FALLBACK, greeting_name
from email_app.models import EmailTemplateOverride
from email_app.services.email_service import TEMPLATES_DIR, EmailService
from email_app.services.preview_contexts import get_preview_context

User = get_user_model()

# Matches ``Hi {{ user_name }},`` and ``Hi {{ user_name|default:"there" }},``
# as well as the ``member_name`` variant used by sprint_partner_intro.md.
GREETING_RE = re.compile(r'Hi \{\{ ?(user_name|member_name)[^}]*\}\},')

# Recognisable local-part: if any greeting starts deriving names from the
# email again, this string surfaces in the rendered body.
HANDLE = 'zzhandle'
NAMELESS_EMAIL = f'{HANDLE}@example.com'


def _greeting_context(template_name, user):
    """Preview context with every greeting placeholder removed.

    The point of these tests is what reaches the greeting on a real send, so
    any ``Ada`` placeholder that would shadow it is stripped. ``user_name``
    is then injected by ``EmailService`` itself. ``member_name`` is not
    injected -- ``plans.services.partner_intro_emails`` supplies it -- so the
    production expression is reproduced here; that call site is pinned
    directly by ``plans.tests.test_partner_intro_greeting_1591``.

    ``user_email`` is pinned to a neutral address because a few staff-facing
    templates legitimately print the recipient address.
    """
    context = get_preview_context(template_name)
    for key in ('user_name', 'user_first_name'):
        context.pop(key, None)
    if 'member_name' in context:
        context['member_name'] = greeting_name(user) or GREETING_FALLBACK
    context['user_email'] = 'recipient-address@example.com'
    return context


class GreetingFallbackRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nameless = User.objects.create_user(
            email=NAMELESS_EMAIL, password='x',
        )
        cls.full_name = User.objects.create_user(
            email='ada@example.com',
            password='x',
            first_name='Ada',
            last_name='Lovelace',
        )
        cls.first_only = User.objects.create_user(
            email='grace@example.com', password='x', first_name='Ada',
        )

    def test_nameless_user_gets_hi_there(self):
        _subject, body = EmailService()._render_template(
            'welcome', self.nameless, _greeting_context('welcome', self.nameless),
        )
        self.assertIn('Hi there,', body)
        self.assertNotIn(HANDLE, body)

    def test_named_user_gets_their_full_name(self):
        _subject, body = EmailService()._render_template(
            'welcome', self.full_name, _greeting_context('welcome', self.full_name),
        )
        self.assertIn('Hi Ada Lovelace,', body)
        self.assertNotIn('Hi there,', body)

    def test_first_name_only_user_gets_their_first_name(self):
        _subject, body = EmailService()._render_template(
            'welcome', self.first_only, _greeting_context('welcome', self.first_only),
        )
        self.assertIn('Hi Ada,', body)
        self.assertNotIn('Hi there,', body)

    def test_every_greeting_template_renders_hi_there_for_a_nameless_user(self):
        """Iterate the template directory rather than hardcoding 35 cases."""
        service = EmailService()
        checked = []
        offenders = []
        for path in sorted(TEMPLATES_DIR.glob('*.md')):
            if not GREETING_RE.search(path.read_text(encoding='utf-8')):
                continue
            name = path.stem
            checked.append(name)
            _subject, body = service._render_template(
                name, self.nameless, _greeting_context(name, self.nameless),
            )
            if 'Hi there,' not in body:
                offenders.append(name)
            if 'Hi ,' in body:
                offenders.append(f'{name} (rendered an empty greeting)')

        # A floor, not an exact count: its only job is to prove the loop
        # actually ran. 37 is today's baseline; adding a new greeting
        # template must not fail this assertion.
        self.assertGreaterEqual(
            len(checked),
            37,
            'Expected at least 37 greeting templates; found '
            f'{len(checked)}: {checked}',
        )
        self.assertEqual(
            offenders,
            [],
            'These templates did not greet a nameless member with '
            f'"Hi there,": {offenders}',
        )

    def test_no_template_leaks_the_recipient_email_handle(self):
        """Durable guard: rendering any template for a nameless member must
        never surface the email local-part."""
        service = EmailService()
        offenders = []
        for path in sorted(TEMPLATES_DIR.glob('*.md')):
            name = path.stem
            subject, body = service._render_template(
                name, self.nameless, _greeting_context(name, self.nameless),
            )
            if HANDLE in body or HANDLE in subject:
                offenders.append(name)

        self.assertEqual(
            offenders,
            [],
            'Email templates must never render the recipient email '
            'local-part as a name. Use accounts.utils.display.greeting_name '
            f'for greetings. Offenders: {offenders}',
        )


class GreetingFallbackAppliesToDatabaseOverridesTest(TestCase):
    """The reason the fallback lives at the injection site and not in the
    ``.md`` files."""

    @classmethod
    def setUpTestData(cls):
        cls.nameless = User.objects.create_user(
            email=NAMELESS_EMAIL, password='x',
        )
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject='Welcome',
            body_markdown='Hi {{ user_name }},\n\nOperator edited copy.\n',
        )

    def test_override_still_reading_the_bare_variable_renders_hi_there(self):
        _subject, body = EmailService()._render_template(
            'welcome', self.nameless, {},
        )
        self.assertIn('Hi there,', body)
        self.assertNotIn('Hi ,', body)
        self.assertNotIn(HANDLE, body)
