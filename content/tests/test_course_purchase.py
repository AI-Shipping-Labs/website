"""Tests for individual course purchases via Stripe - issue #122.

Covers:
- CourseAccess model fields and constraints
- can_access() with CourseAccess (individual purchase grants access)
- Course model new fields (individual_price_eur, stripe_product_id, stripe_price_id)
- Course detail view shows "Buy this course" button when appropriate
- POST /api/courses/{slug}/purchase endpoint
- Webhook handler creates CourseAccess on course purchase checkout
- Existing tier-based access continues to work unchanged
- Studio course form shows individual pricing fields
- Studio "Create Stripe Product" button endpoint
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag

from content.access import can_access
from content.models import Course, CourseAccess, Module, Unit
from payments.services import handle_checkout_completed
from tests.fixtures import TierSetupMixin

User = get_user_model()


# ============================================================
# CourseAccess Model Tests
# ============================================================


@tag('core')
class CourseAccessModelTest(TestCase):
    """Test CourseAccess model fields and constraints."""

    def setUp(self):
        self.user = User.objects.create_user(email='buyer@test.com')
        self.course = Course.objects.create(
            title='Purchase Course', slug='purchase-course',
        )

    def test_create_purchased_access(self):
        access = CourseAccess.objects.create(
            user=self.user,
            course=self.course,
            access_type='purchased',
            stripe_session_id='cs_test_123',
        )
        self.assertEqual(access.user, self.user)
        self.assertEqual(access.course, self.course)
        self.assertEqual(access.access_type, 'purchased')
        self.assertEqual(access.stripe_session_id, 'cs_test_123')
        self.assertIsNone(access.granted_by)
        self.assertIsNotNone(access.created_at)

    def test_create_granted_access(self):
        admin_user = User.objects.create_user(email='admin@test.com')
        access = CourseAccess.objects.create(
            user=self.user,
            course=self.course,
            access_type='granted',
            granted_by=admin_user,
        )
        self.assertEqual(access.access_type, 'granted')
        self.assertEqual(access.granted_by, admin_user)
        self.assertEqual(access.stripe_session_id, '')

    def test_granted_by_set_null_on_delete(self):
        admin_user = User.objects.create_user(email='admin@test.com')
        access = CourseAccess.objects.create(
            user=self.user, course=self.course,
            access_type='granted', granted_by=admin_user,
        )
        admin_user.delete()
        access.refresh_from_db()
        self.assertIsNone(access.granted_by)


# ============================================================
# Course Model New Fields Tests
# ============================================================


# ============================================================
# Access Control Tests
# ============================================================


@tag('core')
class CanAccessWithCourseAccessTest(TierSetupMixin, TestCase):
    """Test that can_access() checks CourseAccess in addition to tier level.

    Consolidated in #261: 5 access-grant variations collapsed into one
    parameterized matrix; the 3 cross-cutting invariants (open course,
    article isolation, staff bypass) stay as standalone tests because
    they exercise different code paths from the grant matrix.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='access@test.com')
        # User has free tier (level 0)
        self.paid_course = Course.objects.create(
            title='Paid Course', slug='paid-access-test',
            status='published', required_level=20,
        )

    def test_paid_course_access_grant_matrix(self):
        """Each grant pathway (tier upgrade, purchased CourseAccess,
        granted CourseAccess, no grant, anonymous) yields the right
        access decision for a paid course."""
        from django.contrib.auth.models import AnonymousUser

        # Case: no grant -> denied
        self.assertFalse(
            can_access(self.user, self.paid_course),
            "free user with no CourseAccess should be denied",
        )

        # Case: anonymous -> denied
        self.assertFalse(
            can_access(AnonymousUser(), self.paid_course),
            "anonymous user should never have CourseAccess",
        )

        # Case: purchased grant -> allowed
        purchased_user = User.objects.create_user(email='paid@test.com')
        CourseAccess.objects.create(
            user=purchased_user, course=self.paid_course,
            access_type='purchased',
        )
        self.assertTrue(can_access(purchased_user, self.paid_course))

        # Case: granted grant -> allowed
        granted_user = User.objects.create_user(email='granted@test.com')
        CourseAccess.objects.create(
            user=granted_user, course=self.paid_course,
            access_type='granted',
        )
        self.assertTrue(can_access(granted_user, self.paid_course))

        # Case: tier upgrade -> allowed (no CourseAccess needed)
        self.user.tier = self.main_tier
        self.user.save()
        self.assertTrue(can_access(self.user, self.paid_course))

    def test_open_course_accessible_to_all(self):
        open_course = Course.objects.create(
            title='Open', slug='open-access', required_level=0,
        )
        self.assertTrue(can_access(self.user, open_course))

    def test_course_access_does_not_affect_non_course_content(self):
        """CourseAccess only applies to Course objects, not other content."""
        import datetime

        from content.models import Article
        article = Article.objects.create(
            title='Test Article', slug='test-article', required_level=20,
            date=datetime.date(2025, 1, 1),
        )
        # Even with CourseAccess for the paid course, article access is unaffected
        CourseAccess.objects.create(
            user=self.user, course=self.paid_course, access_type='purchased',
        )
        self.assertFalse(can_access(self.user, article))

    def test_staff_user_always_has_access(self):
        staff = User.objects.create_user(
            email='staff@test.com', is_staff=True,
        )
        self.assertTrue(can_access(staff, self.paid_course))


# ============================================================
# Course Detail View Tests - Buy Button
# ============================================================


class CourseDetailBuyButtonTest(TierSetupMixin, TestCase):
    """Test that the course detail page shows 'Buy this course' button correctly."""

    def setUp(self):
        self.client = Client()
        self.course = Course.objects.create(
            title='Buyable Course', slug='buyable-course',
            status='published', required_level=20,
            individual_price_eur=Decimal('49.99'),
            stripe_price_id='price_test_123',
        )

    def test_anonymous_user_does_not_see_buy_button(self):
        """Anonymous users should not see the buy button (they need to log in first)."""
        response = self.client.get('/courses/buyable-course')
        self.assertNotContains(response, 'buy-course-btn')

    def test_free_user_sees_buy_button(self):
        """A free user who lacks tier access sees the buy button."""
        User.objects.create_user(email='free@test.com', password='testpass')
        self.client.login(email='free@test.com', password='testpass')
        response = self.client.get('/courses/buyable-course')
        self.assertContains(response, 'buy-course-btn')
        self.assertContains(response, 'Buy this course for EUR 49.99')

    def test_main_user_does_not_see_buy_button(self):
        """A user with tier access should not see the buy button."""
        user = User.objects.create_user(email='main@test.com', password='testpass')
        user.tier = self.main_tier
        user.save()
        self.client.login(email='main@test.com', password='testpass')
        response = self.client.get('/courses/buyable-course')
        self.assertNotContains(response, 'buy-course-btn')

    def test_no_buy_button_when_no_individual_price(self):
        """Course without individual_price_eur should not show buy button."""
        Course.objects.create(
            title='No Buy', slug='no-buy',
            status='published', required_level=20,
        )
        User.objects.create_user(email='nobuy@test.com', password='testpass')
        self.client.login(email='nobuy@test.com', password='testpass')
        response = self.client.get('/courses/no-buy')
        self.assertNotContains(response, 'buy-course-btn')

    def test_user_with_course_access_sees_content(self):
        """User with CourseAccess should see clickable unit links, not the buy button."""
        user = User.objects.create_user(email='purchased@test.com', password='testpass')
        CourseAccess.objects.create(
            user=user, course=self.course, access_type='purchased',
        )
        module = Module.objects.create(course=self.course, title='M1', slug='m1', sort_order=1)
        Unit.objects.create(module=module, title='U1', slug='u1', sort_order=1)
        self.client.login(email='purchased@test.com', password='testpass')
        response = self.client.get('/courses/buyable-course')
        self.assertNotContains(response, 'buy-course-btn')
        self.assertContains(response, 'href="/courses/buyable-course/m1/u1"')

    def test_buy_button_shows_subscription_cta_alongside(self):
        """The buy button appears alongside the subscription CTA."""
        User.objects.create_user(email='both@test.com', password='testpass')
        self.client.login(email='both@test.com', password='testpass')
        response = self.client.get('/courses/buyable-course')
        # Both subscription CTA and buy button should be present
        self.assertContains(response, 'View Pricing')
        self.assertContains(response, 'Buy this course')


# ============================================================
# API: Course Purchase Checkout Tests
# ============================================================


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
@tag('core')
class ApiCoursePurchaseTest(TierSetupMixin, TestCase):
    """Test POST /api/courses/{slug}/purchase endpoint."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email='buyer@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Buy Me', slug='buy-me',
            status='published', required_level=20,
            individual_price_eur=Decimal('29.00'),
            stripe_price_id='price_buy_me',
        )

    def test_anonymous_returns_401(self):
        response = self.client.post('/api/courses/buy-me/purchase')
        self.assertEqual(response.status_code, 401)

    def test_already_has_access_returns_400(self):
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='buyer@test.com', password='testpass')
        response = self.client.post('/api/courses/buy-me/purchase')
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('already have access', data['error'])

    def test_no_individual_price_returns_400(self):
        Course.objects.create(
            title='No Price', slug='no-price-api',
            status='published', required_level=20,
        )
        self.client.login(email='buyer@test.com', password='testpass')
        response = self.client.post('/api/courses/no-price-api/purchase')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not available', response.json()['error'])

    def test_no_stripe_price_id_returns_400(self):
        Course.objects.create(
            title='No Stripe', slug='no-stripe-api',
            status='published', required_level=20,
            individual_price_eur=Decimal('10.00'),
        )
        self.client.login(email='buyer@test.com', password='testpass')
        response = self.client.post('/api/courses/no-stripe-api/purchase')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Stripe pricing not configured', response.json()['error'])

    def test_nonexistent_course_returns_404(self):
        self.client.login(email='buyer@test.com', password='testpass')
        response = self.client.post('/api/courses/nonexistent/purchase')
        self.assertEqual(response.status_code, 404)

    def test_draft_course_returns_404(self):
        Course.objects.create(
            title='Draft', slug='draft-purchase',
            status='draft', individual_price_eur=Decimal('10'),
            stripe_price_id='price_draft',
        )
        self.client.login(email='buyer@test.com', password='testpass')
        response = self.client.post('/api/courses/draft-purchase/purchase')
        self.assertEqual(response.status_code, 404)

    @patch('payments.services._get_stripe_client')
    def test_creates_checkout_session(self, mock_get_client):
        """Successful checkout creation returns checkout_url."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/cs_test_purchase'
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        self.client.login(email='buyer@test.com', password='testpass')
        response = self.client.post('/api/courses/buy-me/purchase')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['checkout_url'], 'https://checkout.stripe.com/cs_test_purchase')

    @patch('payments.services._get_stripe_client')
    def test_checkout_session_uses_payment_mode(self, mock_get_client):
        """Checkout session is created with mode='payment' (not subscription)."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/cs_test'
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        self.client.login(email='buyer@test.com', password='testpass')
        self.client.post('/api/courses/buy-me/purchase')

        call_params = mock_client.checkout.sessions.create.call_args[1]['params']
        self.assertEqual(call_params['mode'], 'payment')

    @patch('payments.services._get_stripe_client')
    def test_checkout_session_includes_course_metadata(self, mock_get_client):
        """Checkout session includes course_id in metadata."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/cs_test'
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        self.client.login(email='buyer@test.com', password='testpass')
        self.client.post('/api/courses/buy-me/purchase')

        call_params = mock_client.checkout.sessions.create.call_args[1]['params']
        self.assertEqual(call_params['metadata']['course_id'], str(self.course.pk))
        self.assertEqual(call_params['metadata']['user_id'], str(self.user.pk))

    @patch('payments.services._get_stripe_client')
    def test_checkout_uses_stripe_customer_id_if_available(self, mock_get_client):
        """If user has stripe_customer_id, use it instead of email."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/cs_test'
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        self.user.stripe_customer_id = 'cus_existing'
        self.user.save()

        self.client.login(email='buyer@test.com', password='testpass')
        self.client.post('/api/courses/buy-me/purchase')

        call_params = mock_client.checkout.sessions.create.call_args[1]['params']
        self.assertEqual(call_params['customer'], 'cus_existing')
        self.assertNotIn('customer_email', call_params)

    @patch('payments.services._get_stripe_client')
    def test_stripe_error_returns_500(self, mock_get_client):
        mock_get_client.side_effect = Exception('Stripe API error')

        self.client.login(email='buyer@test.com', password='testpass')
        with self.assertLogs('content.views.courses', level='ERROR') as logs:
            response = self.client.post('/api/courses/buy-me/purchase')
        self.assertEqual(response.status_code, 500)
        self.assertIn(
            'Failed to create course purchase checkout for course buy-me',
            logs.output[0],
        )
        self.assertIn('error', response.json())

    def test_get_method_not_allowed(self):
        self.client.login(email='buyer@test.com', password='testpass')
        response = self.client.get('/api/courses/buy-me/purchase')
        self.assertEqual(response.status_code, 405)


# ============================================================
# Webhook Handler Tests - Course Purchase
# ============================================================


@tag('core')
class WebhookCoursePurchaseTest(TierSetupMixin, TestCase):
    """Test that checkout.session.completed with course_id creates CourseAccess."""

    def setUp(self):
        self.user = User.objects.create_user(email='webhook@test.com')
        self.course = Course.objects.create(
            title='Webhook Course', slug='webhook-course',
            status='published', required_level=20,
            individual_price_eur=Decimal('49.00'),
        )

    def test_creates_course_access(self):
        session_data = {
            'id': 'cs_purchase_1',
            'customer': 'cus_purchase',
            'customer_details': {'email': 'webhook@test.com'},
            'subscription': '',
            'client_reference_id': str(self.user.pk),
            'metadata': {
                'user_id': str(self.user.pk),
                'course_id': str(self.course.pk),
            },
        }
        handle_checkout_completed(session_data)

        self.assertTrue(
            CourseAccess.objects.filter(
                user=self.user, course=self.course,
            ).exists()
        )
        access = CourseAccess.objects.get(user=self.user, course=self.course)
        self.assertEqual(access.access_type, 'purchased')
        self.assertEqual(access.stripe_session_id, 'cs_purchase_1')

    def test_does_not_change_user_tier(self):
        """Course purchase should NOT update user tier."""
        session_data = {
            'id': 'cs_purchase_2',
            'customer': 'cus_notier',
            'customer_details': {'email': 'webhook@test.com'},
            'subscription': '',
            'client_reference_id': str(self.user.pk),
            'metadata': {
                'user_id': str(self.user.pk),
                'course_id': str(self.course.pk),
            },
        }
        original_tier = self.user.tier
        handle_checkout_completed(session_data)

        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, original_tier)

    def test_stores_stripe_customer_id(self):
        """Course purchase saves stripe_customer_id if not already set."""
        session_data = {
            'id': 'cs_purchase_cid',
            'customer': 'cus_new_buyer',
            'customer_details': {'email': 'webhook@test.com'},
            'subscription': '',
            'client_reference_id': str(self.user.pk),
            'metadata': {
                'user_id': str(self.user.pk),
                'course_id': str(self.course.pk),
            },
        }
        handle_checkout_completed(session_data)

        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_customer_id, 'cus_new_buyer')

    def test_idempotent_on_duplicate(self):
        """Processing the same checkout twice does not create duplicate access."""
        session_data = {
            'id': 'cs_purchase_dup',
            'customer': 'cus_dup',
            'customer_details': {'email': 'webhook@test.com'},
            'subscription': '',
            'client_reference_id': str(self.user.pk),
            'metadata': {
                'user_id': str(self.user.pk),
                'course_id': str(self.course.pk),
            },
        }
        handle_checkout_completed(session_data)
        handle_checkout_completed(session_data)

        self.assertEqual(
            CourseAccess.objects.filter(
                user=self.user, course=self.course,
            ).count(),
            1,
        )

    def test_no_error_when_course_not_found(self):
        """Handler does not crash when course_id is invalid."""
        session_data = {
            'id': 'cs_purchase_nocourse',
            'customer': 'cus_nocourse',
            'customer_details': {'email': 'webhook@test.com'},
            'subscription': '',
            'client_reference_id': str(self.user.pk),
            'metadata': {
                'user_id': str(self.user.pk),
                'course_id': '999999',
            },
        }
        # Should not raise
        handle_checkout_completed(session_data)
        self.assertEqual(CourseAccess.objects.count(), 0)

    def test_no_error_when_user_not_found(self):
        """Handler does not crash when user cannot be found."""
        session_data = {
            'id': 'cs_purchase_nouser',
            'customer': 'cus_nouser',
            'customer_details': {'email': 'nobody@test.com'},
            'subscription': '',
            'client_reference_id': None,
            'metadata': {
                'course_id': str(self.course.pk),
            },
        }
        # Should not raise
        handle_checkout_completed(session_data)
        self.assertEqual(CourseAccess.objects.count(), 0)

    def test_subscription_checkout_still_works(self):
        """Checkout without course_id still updates tier as before."""
        session_data = {
            'id': 'cs_sub_test',
            'customer': 'cus_sub',
            'customer_details': {'email': 'webhook@test.com'},
            'subscription': 'sub_test',
            'client_reference_id': str(self.user.pk),
            'metadata': {
                'user_id': str(self.user.pk),
                'tier_slug': 'main',
            },
        }
        with (
            patch('payments.services._get_subscription_period_end', return_value=None),
            patch('payments.services._get_subscription_price_id', return_value=''),
        ):
            handle_checkout_completed(session_data)

        self.user.refresh_from_db()
        self.assertEqual(self.user.tier.slug, 'main')
        # No CourseAccess should be created
        self.assertEqual(CourseAccess.objects.count(), 0)

    def test_user_found_by_email_when_no_client_reference_id(self):
        """User can be found by email when client_reference_id is absent."""
        session_data = {
            'id': 'cs_purchase_email',
            'customer': 'cus_email_buyer',
            'customer_details': {'email': 'webhook@test.com'},
            'subscription': '',
            'client_reference_id': None,
            'metadata': {
                'course_id': str(self.course.pk),
            },
        }
        handle_checkout_completed(session_data)

        self.assertTrue(
            CourseAccess.objects.filter(
                user=self.user, course=self.course,
            ).exists()
        )


# ============================================================
# Studio Course Form Tests
# ============================================================


class StudioCourseFormIndividualPriceTest(TestCase):
    """Test that studio course form shows individual pricing fields."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_url_removed(self):
        """Course create URL removed — content managed via GitHub sync."""
        response = self.client.get('/studio/courses/new')
        self.assertEqual(response.status_code, 404)

    def test_edit_form_shows_individual_price_field(self):
        course = Course.objects.create(
            title='Edit Test', slug='edit-test',
            individual_price_eur=Decimal('29.99'),
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertContains(response, 'individual_price_eur')
        self.assertContains(response, '29.99')

    def test_edit_saves_individual_price(self):
        """Editing a non-synced course still works for individual price."""
        course = Course.objects.create(
            title='Price Edit', slug='price-edit', status='draft',
            required_level=20,
        )
        self.client.post(f'/studio/courses/{course.pk}/edit', {
            'title': 'Price Edit',
            'slug': 'price-edit',
            'status': 'draft',
            'required_level': '20',
            'individual_price_eur': '49.99',
        })
        course.refresh_from_db()
        self.assertEqual(course.individual_price_eur, Decimal('49.99'))

    def test_edit_course_update_individual_price(self):
        course = Course.objects.create(
            title='Update Price', slug='update-price',
        )
        self.client.post(f'/studio/courses/{course.pk}/edit', {
            'title': 'Update Price',
            'slug': 'update-price',
            'status': 'draft',
            'required_level': '0',
            'individual_price_eur': '19.99',
        })
        course.refresh_from_db()
        self.assertEqual(course.individual_price_eur, Decimal('19.99'))

    def test_edit_course_clear_individual_price(self):
        course = Course.objects.create(
            title='Clear Price', slug='clear-price',
            individual_price_eur=Decimal('29.99'),
        )
        self.client.post(f'/studio/courses/{course.pk}/edit', {
            'title': 'Clear Price',
            'slug': 'clear-price',
            'status': 'draft',
            'required_level': '0',
            'individual_price_eur': '',
        })
        course.refresh_from_db()
        self.assertIsNone(course.individual_price_eur)

    def test_edit_form_shows_stripe_ids_when_set(self):
        course = Course.objects.create(
            title='Has Stripe', slug='has-stripe',
            individual_price_eur=Decimal('10'),
            stripe_product_id='prod_abc',
            stripe_price_id='price_xyz',
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertContains(response, 'prod_abc')
        self.assertContains(response, 'price_xyz')

    def test_edit_form_shows_create_stripe_button_when_needed(self):
        """Show Create Stripe Product button when price is set but no stripe IDs."""
        course = Course.objects.create(
            title='Needs Stripe', slug='needs-stripe',
            individual_price_eur=Decimal('25.00'),
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertContains(response, 'create-stripe-product-btn')
        self.assertContains(response, 'Create Stripe Product')

    def test_edit_form_hides_create_button_when_stripe_exists(self):
        """Do not show Create Stripe Product button when stripe IDs exist."""
        course = Course.objects.create(
            title='Has All', slug='has-all',
            individual_price_eur=Decimal('25.00'),
            stripe_product_id='prod_existing',
            stripe_price_id='price_existing',
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertNotContains(response, 'create-stripe-product-btn')

    def test_edit_form_hides_create_button_when_no_price(self):
        """Do not show Create Stripe Product button when no individual price."""
        course = Course.objects.create(
            title='No Price', slug='no-price-btn',
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertNotContains(response, 'create-stripe-product-btn')


# ============================================================
# Studio Create Stripe Product Endpoint Tests
# ============================================================


class StudioCreateStripeProductTest(TestCase):
    """Test the Create Stripe Product endpoint for courses."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Stripe Product Course', slug='stripe-product',
            individual_price_eur=Decimal('39.99'),
        )

    @patch('payments.services._get_stripe_client')
    def test_creates_stripe_product_and_price(self, mock_get_client):
        mock_client = MagicMock()
        mock_product = MagicMock()
        mock_product.id = 'prod_test_123'
        mock_price = MagicMock()
        mock_price.id = 'price_test_456'
        mock_client.products.create.return_value = mock_product
        mock_client.prices.create.return_value = mock_price
        mock_get_client.return_value = mock_client

        response = self.client.post(
            f'/studio/courses/{self.course.pk}/create-stripe-product',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['product_id'], 'prod_test_123')
        self.assertEqual(data['price_id'], 'price_test_456')

        self.course.refresh_from_db()
        self.assertEqual(self.course.stripe_product_id, 'prod_test_123')
        self.assertEqual(self.course.stripe_price_id, 'price_test_456')

    @patch('payments.services._get_stripe_client')
    def test_price_in_cents(self, mock_get_client):
        """Price is sent to Stripe as cents (unit_amount)."""
        mock_client = MagicMock()
        mock_product = MagicMock()
        mock_product.id = 'prod_cents'
        mock_price = MagicMock()
        mock_price.id = 'price_cents'
        mock_client.products.create.return_value = mock_product
        mock_client.prices.create.return_value = mock_price
        mock_get_client.return_value = mock_client

        self.client.post(
            f'/studio/courses/{self.course.pk}/create-stripe-product',
        )

        price_params = mock_client.prices.create.call_args[1]['params']
        self.assertEqual(price_params['unit_amount'], 3999)  # 39.99 EUR * 100
        self.assertEqual(price_params['currency'], 'eur')

    def test_already_has_stripe_product_returns_400(self):
        self.course.stripe_product_id = 'prod_existing'
        self.course.save()
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/create-stripe-product',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('already has', response.json()['error'])

    def test_no_individual_price_returns_400(self):
        course = Course.objects.create(
            title='No Price', slug='no-price-stripe',
        )
        response = self.client.post(
            f'/studio/courses/{course.pk}/create-stripe-product',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('individual_price_eur', response.json()['error'])

    def test_nonexistent_course_returns_404(self):
        response = self.client.post('/studio/courses/99999/create-stripe-product')
        self.assertEqual(response.status_code, 404)

    def test_get_returns_405(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/create-stripe-product',
        )
        self.assertEqual(response.status_code, 405)

    def test_non_staff_returns_403(self):
        User.objects.create_user(
            email='regular@test.com', password='testpass',
        )
        client = Client()
        client.login(email='regular@test.com', password='testpass')
        response = client.post(
            f'/studio/courses/{self.course.pk}/create-stripe-product',
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirects_to_login(self):
        client = Client()
        response = client.post(
            f'/studio/courses/{self.course.pk}/create-stripe-product',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    @patch('payments.services._get_stripe_client')
    def test_stripe_error_returns_500(self, mock_get_client):
        mock_get_client.side_effect = Exception('Stripe API error')
        with self.assertLogs('studio.views.courses', level='ERROR') as logs:
            response = self.client.post(
                f'/studio/courses/{self.course.pk}/create-stripe-product',
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn(
            f'Failed to create Stripe product for course {self.course.pk}',
            logs.output[0],
        )
        self.assertIn('error', response.json())


# ============================================================
# Course Unit Access with CourseAccess
# ============================================================


@tag('core')
class CourseUnitAccessWithPurchaseTest(TierSetupMixin, TestCase):
    """Test that individual course purchasers can access course units."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email='unit@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Unit Access Course', slug='unit-access',
            status='published', required_level=20,
        )
        self.module = Module.objects.create(
            course=self.course, title='Module 1', slug='module-1', sort_order=1,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Lesson 1', slug='lesson-1', sort_order=1,
        )

    def test_free_user_cannot_access_unit(self):
        self.client.login(email='unit@test.com', password='testpass')
        response = self.client.get('/courses/unit-access/module-1/lesson-1')
        self.assertEqual(response.status_code, 403)

    def test_purchased_user_can_access_unit(self):
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='purchased',
        )
        self.client.login(email='unit@test.com', password='testpass')
        response = self.client.get('/courses/unit-access/module-1/lesson-1')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lesson 1')

    def test_granted_user_can_access_unit(self):
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='granted',
        )
        self.client.login(email='unit@test.com', password='testpass')
        response = self.client.get('/courses/unit-access/module-1/lesson-1')
        self.assertEqual(response.status_code, 200)

    def test_tier_access_still_works_for_units(self):
        self.user.tier = self.main_tier
        self.user.save()
        self.client.login(email='unit@test.com', password='testpass')
        response = self.client.get('/courses/unit-access/module-1/lesson-1')
        self.assertEqual(response.status_code, 200)


# ============================================================
# Admin Tests
# ============================================================


class CourseAccessAdminTest(TestCase):
    """Test CourseAccess admin registration."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_course_access_list(self):
        response = self.client.get('/admin/content/courseaccess/')
        self.assertEqual(response.status_code, 200)

    def test_admin_course_access_add(self):
        response = self.client.get('/admin/content/courseaccess/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_course_edit_shows_individual_pricing(self):
        """Course admin shows individual purchase fields."""
        course = Course.objects.create(
            title='Admin Course', slug='admin-course',
            individual_price_eur=Decimal('19.99'),
        )
        response = self.client.get(f'/admin/content/course/{course.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'individual_price_eur')
