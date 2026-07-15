"""Onboarding paid-gating tests (issue #982).

Onboarding feeds the personalized plan and the 1:1 founder call -- both
paid-member benefits. This module verifies the SINGLE shared predicate
``questionnaires.onboarding.can_access_onboarding`` (effective tier
``>= LEVEL_BASIC`` via the override-aware ``content.access.get_user_level``)
backs every onboarding surface:

- the dashboard onboarding prompt banner,
- the ``/onboarding/...`` form views (start / identify / questions /
  numeric back-compat / submit),
- the AI chat views (chat / message / stream),
- the request-a-call ``Finish onboarding`` CTA.

The contract for an ineligible authenticated member (Free base, no active
override, or expired override) is: entry points are HIDDEN, direct hits to
``/onboarding/...`` redirect to ``/`` (no 403/500), and no state is mutated
/ no LLM turn runs. Anonymous users keep ``@login_required`` (redirect to
login). Staff / superusers resolve to ``LEVEL_PREMIUM`` and keep access.

The four personas + the generic ``onboarding-general`` questionnaire are
seeded by migration ``questionnaires.0003`` and present in the test DB.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier
from questionnaires.models import OnboardingConversation, Questionnaire, Response
from questionnaires.onboarding import (
    GENERIC_ONBOARDING_SLUG,
    can_access_onboarding,
)

User = get_user_model()

LLM_ON = override_settings(
    LLM_API_KEY='sk-test-fake', LLM_PROVIDER='anthropic',
    ONBOARDING_AI_ENABLED='true', ONBOARDING_AI_STREAMING='true',
)


def _tier(slug):
    return Tier.objects.get(slug=slug)


def _make_user(email, slug='free'):
    """Create a member on the named tier (``free`` by default)."""
    return User.objects.create_user(
        email=email, password='pw', tier=_tier(slug),
    )


def _add_override(user, override_slug='main', *, days=14, is_active=True):
    """Attach a TierOverride to ``user`` expiring ``days`` from now.

    A negative ``days`` makes the override already-expired so the
    override-aware resolver treats the member as their base tier.
    """
    return TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=_tier(override_slug),
        expires_at=timezone.now() + datetime.timedelta(days=days),
        is_active=is_active,
    )


# --------------------------------------------------------------------------
# The shared predicate (the one gate behind every surface).
# --------------------------------------------------------------------------


@tag('core')
class CanAccessOnboardingPredicateTest(TestCase):
    """``can_access_onboarding`` is the single override-aware gate."""

    def test_free_base_no_override_denied(self):
        self.assertFalse(can_access_onboarding(_make_user('free@t.com')))

    def test_basic_allowed(self):
        self.assertTrue(can_access_onboarding(_make_user('b@t.com', 'basic')))

    def test_main_allowed(self):
        self.assertTrue(can_access_onboarding(_make_user('m@t.com', 'main')))

    def test_premium_allowed(self):
        self.assertTrue(can_access_onboarding(_make_user('p@t.com', 'premium')))

    def test_anonymous_denied(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(can_access_onboarding(AnonymousUser()))

    def test_free_base_active_main_override_allowed(self):
        user = _make_user('ov@t.com')
        _add_override(user, 'main')
        self.assertTrue(can_access_onboarding(user))

    def test_free_base_expired_override_denied(self):
        user = _make_user('exp@t.com')
        _add_override(user, 'main', days=-1)
        self.assertFalse(can_access_onboarding(user))

    def test_free_base_inactive_override_denied(self):
        user = _make_user('inact@t.com')
        _add_override(user, 'main', is_active=False)
        self.assertFalse(can_access_onboarding(user))

    def test_staff_allowed(self):
        user = _make_user('staff@t.com')
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        self.assertTrue(can_access_onboarding(user))

    def test_superuser_allowed(self):
        user = _make_user('super@t.com')
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])
        self.assertTrue(can_access_onboarding(user))


# --------------------------------------------------------------------------
# Form views: /onboarding/ start / identify / questions / <id> / submit.
# --------------------------------------------------------------------------


@override_settings(ONBOARDING_AI_ENABLED='false')
@tag('core')
class FormViewGatingTest(TestCase):
    """Direct hits to the form flow by an ineligible member redirect to ``/``."""

    @classmethod
    def setUpTestData(cls):
        cls.free = _make_user('form-free@t.com')
        cls.basic = _make_user('form-basic@t.com', 'basic')

    def _assert_redirect_home(self, resp):
        self.assertEqual(resp.status_code, 302)
        # Redirect goes to the dashboard, never to login or onboarding.
        self.assertEqual(resp['Location'], '/')

    def test_free_start_get_redirects_home(self):
        self.client.force_login(self.free)
        self._assert_redirect_home(self.client.get('/onboarding/'))

    def test_basic_start_get_renders(self):
        self.client.force_login(self.basic)
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 200)

    def test_free_questions_get_redirects_home(self):
        self.client.force_login(self.free)
        self._assert_redirect_home(self.client.get('/onboarding/questions'))

    def test_free_numeric_detail_get_redirects_home(self):
        # The numeric back-compat URL must gate BEFORE the respondent-scoped
        # 404 -- an ineligible member is sent home, not handed a 404.
        self.client.force_login(self.free)
        self._assert_redirect_home(self.client.get('/onboarding/1'))

    def test_free_identify_post_creates_no_response(self):
        self.client.force_login(self.free)
        resp = self.client.post(
            reverse('onboarding_identify'), {'self_id': 'none'},
        )
        self._assert_redirect_home(resp)
        self.assertFalse(
            Response.objects.filter(respondent=self.free).exists(),
            'a Free identify POST must not create an onboarding Response',
        )

    def test_basic_identify_post_creates_response(self):
        self.client.force_login(self.basic)
        resp = self.client.post(
            reverse('onboarding_identify'), {'self_id': 'none'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('onboarding_questions'))
        self.assertTrue(
            Response.objects.filter(respondent=self.basic).exists(),
        )

    def test_free_submit_post_does_not_mark_submitted(self):
        # Pre-create a DRAFT response directly (a Free user cannot reach
        # identify, but we still guarantee submit cannot flip it).
        generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)
        draft = Response.objects.create(
            respondent=self.free, questionnaire=generic, status='draft',
        )
        self.client.force_login(self.free)
        resp = self.client.post(f'/onboarding/{draft.pk}/submit')
        self._assert_redirect_home(resp)
        draft.refresh_from_db()
        self.assertEqual(
            draft.status, 'draft',
            'a Free submit POST must not flip the response to submitted',
        )

    def test_anonymous_start_redirects_to_login(self):
        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])


# --------------------------------------------------------------------------
# Override matrix on the live form flow.
# --------------------------------------------------------------------------


@override_settings(ONBOARDING_AI_ENABLED='false')
@tag('core')
class OverrideMatrixFormFlowTest(TestCase):
    def test_active_main_override_grants_access(self):
        user = _make_user('ov-active@t.com')
        _add_override(user, 'main')
        self.client.force_login(user)

        # Can open the landing.
        self.assertEqual(self.client.get('/onboarding/').status_code, 200)
        # Can create a response via identify.
        resp = self.client.post(
            reverse('onboarding_identify'), {'self_id': 'none'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Response.objects.filter(respondent=user).exists())

    def test_expired_override_denied_redirect_home(self):
        user = _make_user('ov-expired@t.com')
        _add_override(user, 'main', days=-1)
        self.client.force_login(user)

        resp = self.client.get('/onboarding/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/')
        # And identify must not create a response.
        self.client.post(reverse('onboarding_identify'), {'self_id': 'none'})
        self.assertFalse(Response.objects.filter(respondent=user).exists())


# --------------------------------------------------------------------------
# AI chat views: chat / message / stream.
# --------------------------------------------------------------------------


@LLM_ON
@tag('core')
class AiChatGatingTest(TestCase):
    """A Free member hitting the AI chat surface runs no LLM turn."""

    @classmethod
    def setUpTestData(cls):
        cls.free = _make_user('ai-free@t.com')

    def setUp(self):
        self.client.force_login(self.free)

    def test_chat_get_redirects_home(self):
        resp = self.client.get('/onboarding/chat')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/')
        # No conversation was seeded for the ineligible member.
        self.assertFalse(
            OnboardingConversation.objects.filter(
                response__respondent=self.free,
            ).exists(),
        )

    def test_chat_message_post_redirects_and_runs_no_turn(self):
        with patch(
            'accounts.views.onboarding_ai.run_member_turn',
        ) as run_turn:
            resp = self.client.post(
                '/onboarding/chat/message', {'message': 'hi'},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/')
        run_turn.assert_not_called()
        self.assertFalse(Response.objects.filter(respondent=self.free).exists())

    def test_chat_stream_emits_fallback_and_runs_no_turn(self):
        with patch(
            'accounts.views.onboarding_ai.stream_logical_member_turn',
        ) as stream_turn:
            resp = self.client.post(
                '/onboarding/chat/stream', {'message': 'hi'},
            )
            body = b''.join(resp.streaming_content).decode()
        self.assertEqual(resp.status_code, 200)
        # The SSE payload is a single ``fallback`` frame, no ``delta``.
        self.assertIn('event: fallback', body)
        self.assertNotIn('event: delta', body)
        stream_turn.assert_not_called()
        # No transcript / response written for the ineligible member.
        self.assertFalse(Response.objects.filter(respondent=self.free).exists())

    def test_active_override_member_reaches_chat(self):
        user = _make_user('ai-ov@t.com')
        _add_override(user, 'main')
        self.client.force_login(user)
        resp = self.client.get('/onboarding/chat')
        # Eligible: not bounced to ``/`` (renders the chat or routes within
        # the flow, never the paid-gate redirect home).
        self.assertNotEqual(resp.get('Location'), '/')


# --------------------------------------------------------------------------
# Dashboard prompt banner.
# --------------------------------------------------------------------------


@override_settings(ONBOARDING_AI_ENABLED='false')
@tag('core')
class DashboardPromptGatingTest(TestCase):
    def test_free_member_no_banner(self):
        self.client.force_login(_make_user('dash-free@t.com'))
        resp = self.client.get('/')
        self.assertNotContains(resp, 'data-testid="onboarding-prompt"')

    def test_paid_member_uncompleted_shows_banner(self):
        self.client.force_login(_make_user('dash-basic@t.com', 'basic'))
        resp = self.client.get('/')
        self.assertContains(resp, 'data-testid="onboarding-prompt"')

    def test_active_override_member_shows_banner(self):
        user = _make_user('dash-ov@t.com')
        _add_override(user, 'main')
        self.client.force_login(user)
        resp = self.client.get('/')
        self.assertContains(resp, 'data-testid="onboarding-prompt"')

    def test_expired_override_member_no_banner(self):
        user = _make_user('dash-exp@t.com')
        _add_override(user, 'main', days=-1)
        self.client.force_login(user)
        resp = self.client.get('/')
        self.assertNotContains(resp, 'data-testid="onboarding-prompt"')

    def test_paid_member_completed_hides_banner(self):
        user = _make_user('dash-done@t.com', 'basic')
        generic = Questionnaire.objects.get(slug=GENERIC_ONBOARDING_SLUG)
        Response.objects.create(
            respondent=user, questionnaire=generic, status='submitted',
        )
        self.client.force_login(user)
        resp = self.client.get('/')
        self.assertNotContains(resp, 'data-testid="onboarding-prompt"')


# --------------------------------------------------------------------------
# Request-a-call "Finish onboarding" CTA.
# --------------------------------------------------------------------------


@tag('core')
class RequestCallCtaGatingTest(TestCase):
    def test_free_member_not_handed_finish_onboarding_cta(self):
        self.client.force_login(_make_user('rc-free@t.com'))
        resp = self.client.get('/request-a-call')
        self.assertNotContains(
            resp, 'data-testid="request-call-onboarding-cta"',
        )

    def test_paid_member_sees_finish_onboarding_cta(self):
        # A paid member who has not onboarded gets the gate + CTA link.
        self.client.force_login(_make_user('rc-basic@t.com', 'basic'))
        resp = self.client.get('/request-a-call')
        self.assertContains(
            resp, 'data-testid="request-call-onboarding-cta"',
        )

    def test_active_override_member_sees_finish_onboarding_cta(self):
        user = _make_user('rc-ov@t.com')
        _add_override(user, 'main')
        self.client.force_login(user)
        resp = self.client.get('/request-a-call')
        self.assertContains(
            resp, 'data-testid="request-call-onboarding-cta"',
        )
