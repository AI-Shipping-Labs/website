"""Tests for UserAttribution: post_save signal that snapshots UTMs at signup.

Each test exercises the actual signup view (POST to /api/register etc.) so
the signal fires through the real code path. Cookies are set on the test
Client before the request; sessions are seeded by hitting a UTM-bearing
URL first so the middleware writes the last-touch into the session.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from allauth.account.signals import user_signed_up
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from analytics.middleware import (
    ANON_ID_COOKIE,
    FIRST_TOUCH_COOKIE,
    SESSION_LAST_TOUCH,
)
from analytics.models import CampaignVisit, UserAttribution
from integrations.models import UtmCampaign

User = get_user_model()


# Browser-like UA so the bot regex doesn't drop our test traffic.
BROWSER_UA = 'Mozilla/5.0 (test browser)'

# Force inline execution of any background queue tasks (visit logging) so
# assertions can run immediately after the request returns.
SYNC_Q_CLUSTER = {'sync': True, 'orm': 'default', 'name': 'test', 'workers': 1}


def make_browser_client():
    return Client(HTTP_USER_AGENT=BROWSER_UA)


def set_first_touch_cookie(client, source='newsletter', medium='email',
                           campaign='launch_april2026', content='hero',
                           term='', ts=None):
    """Place an `aslab_ft` cookie on the test Client jar."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    payload = {
        'source': source,
        'medium': medium,
        'campaign': campaign,
        'content': content,
        'term': term,
        'ts': ts,
    }
    client.cookies[FIRST_TOUCH_COOKIE] = json.dumps(payload)
    return payload


def set_anon_id_cookie(client, anon_id=None):
    """Place an `aslab_aid` cookie on the test Client jar."""
    anon_id = anon_id or str(uuid.uuid4())
    client.cookies[ANON_ID_COOKIE] = anon_id
    return anon_id


def seed_last_touch_session(client, source='twitter', medium='social',
                            campaign='summer_drop', content='cta_a',
                            term=''):
    """Trigger the middleware to write `aslab_lt` into the session.

    Done by issuing a UTM-bearing GET. Returns the dict the middleware
    placed in the session so the caller can compare against it.
    """
    qs = (
        f'utm_source={source}&utm_medium={medium}'
        f'&utm_campaign={campaign}&utm_content={content}'
    )
    if term:
        qs += f'&utm_term={term}'
    client.get('/?' + qs)
    return client.session.get(SESSION_LAST_TOUCH)


def signup_email_password(client, email='new@test.com', password='pw1234ABcd'):
    """POST to /api/register with the given credentials."""
    return client.post(
        '/api/register',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json',
    )


def signup_newsletter(client, email='subscriber@test.com'):
    """POST to /api/subscribe with the given email."""
    return client.post(
        '/api/subscribe',
        data=json.dumps({'email': email}),
        content_type='application/json',
    )


# --- Email + password signup -------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class EmailPasswordSignupAttributionTest(TestCase):
    """Scenario: email/password signup snapshots UTMs from cookie + session."""

    def test_full_utms_creates_attribution_with_correct_fields(self, _ses):
        c = make_browser_client()
        anon_id = set_anon_id_cookie(c)
        ts_iso = '2026-01-01T12:00:00+00:00'
        first_payload = set_first_touch_cookie(
            c, source='newsletter', medium='email',
            campaign='launch_april2026', content='hero', ts=ts_iso,
        )
        seed_last_touch_session(
            c, source='twitter', medium='social',
            campaign='summer_drop', content='cta_a',
        )

        response = signup_email_password(c, email='alice@test.com')
        self.assertEqual(response.status_code, 201)

        user = User.objects.get(email='alice@test.com')
        attr = UserAttribution.objects.get(user=user)

        # First-touch from cookie
        self.assertEqual(attr.first_touch_utm_source, 'newsletter')
        self.assertEqual(attr.first_touch_utm_medium, 'email')
        self.assertEqual(attr.first_touch_utm_campaign, 'launch_april2026')
        self.assertEqual(attr.first_touch_utm_content, 'hero')
        # ts should match what we put in the cookie
        self.assertEqual(
            attr.first_touch_ts.isoformat(), first_payload['ts']
        )

        # Last-touch from session
        self.assertEqual(attr.last_touch_utm_source, 'twitter')
        self.assertEqual(attr.last_touch_utm_medium, 'social')
        self.assertEqual(attr.last_touch_utm_campaign, 'summer_drop')
        self.assertEqual(attr.last_touch_utm_content, 'cta_a')

        self.assertEqual(attr.signup_path, 'email_password')
        self.assertEqual(attr.anonymous_id, anon_id)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class NewsletterSignupAttributionTest(TestCase):
    """Scenario: newsletter signup snapshots UTMs and uses signup_path=newsletter."""

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
    def test_last_touch_session_only_signup_path_newsletter(self, _ses):
        c = make_browser_client()
        seed_last_touch_session(
            c, source='reddit', medium='social', campaign='ama_q1',
        )
        # The seed request also wrote first-touch cookie — clear it so we
        # exercise the "last-touch only" code path explicitly.
        if FIRST_TOUCH_COOKIE in c.cookies:
            del c.cookies[FIRST_TOUCH_COOKIE]
        response = signup_newsletter(c, email='bob@test.com')
        self.assertEqual(response.status_code, 200)

        user = User.objects.get(email='bob@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.signup_path, 'newsletter')
        self.assertEqual(attr.last_touch_utm_source, 'reddit')
        self.assertEqual(attr.last_touch_utm_campaign, 'ama_q1')
        # No first-touch was set
        self.assertEqual(attr.first_touch_utm_source, '')
        self.assertEqual(attr.first_touch_utm_campaign, '')
        self.assertIsNone(attr.first_touch_ts)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class NoUtmSignupTest(TestCase):
    """Scenario: signup with no UTMs creates an empty row, not nothing, not error."""

    def test_signup_without_any_utm_creates_empty_row(self, _ses):
        c = make_browser_client()
        response = signup_email_password(c, email='nobody@test.com')
        self.assertEqual(response.status_code, 201)

        user = User.objects.get(email='nobody@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.first_touch_utm_source, '')
        self.assertEqual(attr.first_touch_utm_medium, '')
        self.assertEqual(attr.first_touch_utm_campaign, '')
        self.assertEqual(attr.first_touch_utm_content, '')
        self.assertEqual(attr.first_touch_utm_term, '')
        self.assertEqual(attr.last_touch_utm_source, '')
        self.assertEqual(attr.last_touch_utm_campaign, '')
        self.assertIsNone(attr.first_touch_ts)
        self.assertIsNone(attr.last_touch_ts)
        self.assertEqual(attr.signup_path, 'email_password')


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class FirstTouchOnlyTest(TestCase):
    """Scenario: first-touch cookie only, no session — first-touch fields populated."""

    def test_first_touch_only_records_first_touch_leaves_last_empty(self, _ses):
        c = make_browser_client()
        set_first_touch_cookie(
            c, source='newsletter', medium='email',
            campaign='launch_april2026', content='hero',
        )
        # Note: no seed_last_touch_session call
        response = signup_email_password(c, email='charlie@test.com')
        self.assertEqual(response.status_code, 201)

        user = User.objects.get(email='charlie@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.first_touch_utm_source, 'newsletter')
        self.assertEqual(attr.first_touch_utm_campaign, 'launch_april2026')
        # last-touch left empty (do NOT copy from first-touch)
        self.assertEqual(attr.last_touch_utm_source, '')
        self.assertEqual(attr.last_touch_utm_campaign, '')
        self.assertIsNone(attr.last_touch_ts)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class LastTouchOnlyTest(TestCase):
    """Scenario: session has last-touch but no first-touch cookie."""

    def test_last_touch_only_records_last_touch_leaves_first_empty(self, _ses):
        c = make_browser_client()
        seed_last_touch_session(
            c, source='twitter', medium='social', campaign='summer_drop',
        )
        # Confirm no first-touch cookie was set (session was seeded but
        # the first response also wrote first-touch — clear it for this test).
        if FIRST_TOUCH_COOKIE in c.cookies:
            del c.cookies[FIRST_TOUCH_COOKIE]

        response = signup_email_password(c, email='dana@test.com')
        self.assertEqual(response.status_code, 201)

        user = User.objects.get(email='dana@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.last_touch_utm_source, 'twitter')
        self.assertEqual(attr.last_touch_utm_campaign, 'summer_drop')
        self.assertEqual(attr.first_touch_utm_source, '')
        self.assertEqual(attr.first_touch_utm_campaign, '')


# --- Campaign FK resolution ---------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class CampaignFkResolutionTest(TestCase):

    def setUp(self):
        self.campaign = UtmCampaign.objects.create(
            name='Launch April 2026',
            slug='launch_april2026',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )

    def test_first_touch_fk_resolves_when_campaign_exists(self, _ses):
        c = make_browser_client()
        set_first_touch_cookie(c, campaign='launch_april2026')
        signup_email_password(c, email='ella@test.com')
        attr = UserAttribution.objects.get(user__email='ella@test.com')
        self.assertEqual(attr.first_touch_campaign_id, self.campaign.pk)
        self.assertEqual(attr.first_touch_utm_campaign, 'launch_april2026')

    def test_first_touch_fk_null_when_no_matching_campaign(self, _ses):
        c = make_browser_client()
        set_first_touch_cookie(c, campaign='unknown_slug')
        signup_email_password(c, email='frank@test.com')
        attr = UserAttribution.objects.get(user__email='frank@test.com')
        self.assertIsNone(attr.first_touch_campaign_id)
        self.assertEqual(attr.first_touch_utm_campaign, 'unknown_slug')

    def test_last_touch_fk_resolves_when_campaign_exists(self, _ses):
        c = make_browser_client()
        seed_last_touch_session(c, campaign='launch_april2026')
        signup_email_password(c, email='grace@test.com')
        attr = UserAttribution.objects.get(user__email='grace@test.com')
        self.assertEqual(attr.last_touch_campaign_id, self.campaign.pk)


# --- Backfill of CampaignVisit ------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class BackfillCampaignVisitTest(TestCase):
    """Scenario: anonymous CampaignVisit rows get attached to the new user."""

    def test_backfill_links_anonymous_visits_to_new_user(self, _ses):
        anon_id = str(uuid.uuid4())
        # 3 prior anonymous visits with this anon_id
        for path in ('/blog', '/courses', '/pricing'):
            CampaignVisit.objects.create(
                anonymous_id=anon_id,
                utm_source='newsletter',
                utm_campaign='launch',
                path=path,
            )
        # Sanity: no users attached yet
        self.assertEqual(
            CampaignVisit.objects.filter(anonymous_id=anon_id, user_id__isnull=True).count(),
            3,
        )

        c = make_browser_client()
        set_anon_id_cookie(c, anon_id)
        signup_email_password(c, email='helen@test.com')

        user = User.objects.get(email='helen@test.com')
        # All 3 prior visits now belong to helen
        self.assertEqual(
            CampaignVisit.objects.filter(anonymous_id=anon_id, user_id=user.pk).count(),
            3,
        )
        self.assertEqual(
            CampaignVisit.objects.filter(anonymous_id=anon_id, user_id__isnull=True).count(),
            0,
        )

    def test_backfill_does_not_steal_visits_belonging_to_other_users(self, _ses):
        # Existing user with an existing visit
        other = User.objects.create_user(email='owner@test.com', password='pw1234ABcd')
        anon_id = str(uuid.uuid4())
        existing_visit = CampaignVisit.objects.create(
            anonymous_id=anon_id,
            utm_source='newsletter',
            utm_campaign='launch',
            path='/blog',
            user=other,
        )

        # New user signs up with the same anon_id (e.g. shared device)
        c = make_browser_client()
        set_anon_id_cookie(c, anon_id)
        signup_email_password(c, email='intruder@test.com')

        existing_visit.refresh_from_db()
        self.assertEqual(
            existing_visit.user_id, other.pk,
            'Existing visit was reassigned to the new signup',
        )

    def test_backfill_skipped_silently_when_no_anonymous_id(self, _ses):
        # Existing visit with some other anon_id — must not be touched.
        unrelated = CampaignVisit.objects.create(
            anonymous_id='some-other-id',
            utm_source='newsletter',
            utm_campaign='launch',
            path='/blog',
        )
        c = make_browser_client()
        # No anon_id cookie set
        response = signup_email_password(c, email='isaac@test.com')
        self.assertEqual(response.status_code, 201)
        unrelated.refresh_from_db()
        self.assertIsNone(unrelated.user_id)


# --- Stripe checkout user creation --------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class StripeCheckoutSignupTest(TestCase):
    """Scenario: Stripe webhook creates a user → signup_path=stripe_checkout."""

    def test_stripe_user_creation_sets_signup_path_stripe_checkout(self):
        # Ensure the price-id-to-tier lookup succeeds so the handler runs
        # to completion. We intercept after user.save by mocking the parts
        # of services we don't want to actually run.
        from payments.models import Tier
        tier = Tier.objects.filter(slug='main').first()
        if tier is None:
            tier = Tier.objects.create(
                slug='main', name='Main', level=20, monthly_cents=2900,
            )

        session_data = {
            'id': 'cs_test_123',
            'customer': 'cus_test_456',
            'subscription': 'sub_test_789',
            'customer_details': {'email': 'newpaid@test.com'},
            'client_reference_id': None,
            'metadata': {'tier_slug': 'main'},
        }

        # Patch the Stripe lookup so it returns no period-end (we don't
        # care about the post-creation work for this test).
        with patch('payments.services._get_subscription_period_end', return_value=None), \
             patch('payments.services._community_invite'):
            from payments.services import handle_checkout_completed
            handle_checkout_completed(session_data)

        user = User.objects.get(email='newpaid@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.signup_path, 'stripe_checkout')
        # No request was bound, so no UTMs and no anon_id
        self.assertEqual(attr.first_touch_utm_source, '')
        self.assertEqual(attr.last_touch_utm_source, '')
        self.assertEqual(attr.anonymous_id, '')


# --- Social signup ------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class SocialSignupSignupPathTest(TestCase):
    """Scenario: allauth user_signed_up rewrites signup_path for OAuth signups."""

    def _fake_social_signup(self, provider, email):
        """Create a User and fire user_signed_up with a sociallogin."""
        user = User.objects.create_user(email=email, password='pw1234ABcd')

        # Build a minimal SocialAccount-like object the handler can read.
        class FakeAccount:
            def __init__(self, provider):
                self.provider = provider

        class FakeSocialLogin:
            def __init__(self, provider):
                self.account = FakeAccount(provider)

        user_signed_up.send(
            sender=User,
            request=None,
            user=user,
            sociallogin=FakeSocialLogin(provider),
        )
        return user

    def test_slack_oauth_signup_sets_signup_path_slack_oauth(self):
        user = self._fake_social_signup('slack', 'slack-user@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.signup_path, 'slack_oauth')

    def test_google_oauth_signup_sets_signup_path_google_oauth(self):
        user = self._fake_social_signup('google', 'google-user@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.signup_path, 'google_oauth')

    def test_github_oauth_signup_sets_signup_path_github_oauth(self):
        user = self._fake_social_signup('github', 'github-user@test.com')
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.signup_path, 'github_oauth')


# --- Robustness ---------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class SignalRobustnessTest(TestCase):

    def test_signal_does_not_raise_when_no_request_available(self):
        """Direct create_user (e.g. management command) must not crash."""
        user = User.objects.create_user(
            email='cli@test.com', password='pw1234ABcd',
        )
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.signup_path, 'unknown')
        # All UTM fields blank, no anon_id, both ts null
        self.assertEqual(attr.first_touch_utm_source, '')
        self.assertEqual(attr.last_touch_utm_source, '')
        self.assertEqual(attr.anonymous_id, '')
        self.assertIsNone(attr.first_touch_ts)
        self.assertIsNone(attr.last_touch_ts)

    def test_only_one_attribution_row_per_user(self):
        """Saving the user repeatedly must not create extra rows."""
        user = User.objects.create_user(
            email='solo@test.com', password='pw1234ABcd',
        )
        user.email_verified = True
        user.save()
        user.theme_preference = 'dark'
        user.save()
        self.assertEqual(
            UserAttribution.objects.filter(user=user).count(), 1,
        )

    def test_superuser_creation_does_not_raise(self):
        """createsuperuser path must not crash on missing request/UTMs."""
        user = User.objects.create_superuser(
            email='admin@test.com', password='pw1234ABcd',
        )
        attr = UserAttribution.objects.get(user=user)
        self.assertEqual(attr.signup_path, 'unknown')


# --- Edge: malformed cookie ---------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class MalformedCookieTest(TestCase):
    """Bad first-touch JSON must not crash the signup."""

    def test_malformed_first_touch_cookie_creates_empty_first_touch(self, _ses):
        c = make_browser_client()
        c.cookies[FIRST_TOUCH_COOKIE] = 'not-json{{'
        response = signup_email_password(c, email='junk@test.com')
        self.assertEqual(response.status_code, 201)
        attr = UserAttribution.objects.get(user__email='junk@test.com')
        self.assertEqual(attr.first_touch_utm_source, '')
        self.assertEqual(attr.first_touch_utm_campaign, '')
