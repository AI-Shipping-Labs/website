"""Tests for capturing ``customer_details.name`` from Stripe (issue #699).

Covers both the tier-checkout path
(``handle_checkout_completed``) and the course-purchase path
(``_handle_course_purchase``), including the do-not-overwrite rule.
"""

from decimal import Decimal

from django.test import TestCase, tag

from accounts.models import User
from content.models import Course, CourseAccess
from payments.services import handle_checkout_completed


@tag('core')
class StripeNameCaptureTierCheckoutTest(TestCase):
    """``handle_checkout_completed`` populates names from Stripe."""

    def _session(self, user, *, name=""):
        return {
            "id": f"cs_{user.pk}",
            "customer": f"cus_{user.pk}",
            "customer_details": {"email": user.email, "name": name},
            "subscription": "",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

    def test_multi_token_name_is_split_and_saved(self):
        user = User.objects.create_user(email="multi-stripe@test.com")
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")

        handle_checkout_completed(
            self._session(user, name="Salvador Castillo Raya"),
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Salvador Castillo")
        self.assertEqual(user.last_name, "Raya")
        # Tier update was preserved (folded into the same save).
        self.assertEqual(user.tier.slug, "basic")

    def test_single_token_name_fills_first_only(self):
        user = User.objects.create_user(email="madonna-stripe@test.com")

        handle_checkout_completed(self._session(user, name="Madonna"))

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Madonna")
        self.assertEqual(user.last_name, "")
        self.assertEqual(user.tier.slug, "basic")

    def test_does_not_overwrite_existing_name(self):
        user = User.objects.create_user(
            email="kept-stripe@test.com",
            first_name="Custom",
            last_name="Edit",
        )

        handle_checkout_completed(
            self._session(user, name="Wrong Name"),
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Custom")
        self.assertEqual(user.last_name, "Edit")
        # Tier upgrade still applied.
        self.assertEqual(user.tier.slug, "basic")

    def test_missing_name_is_noop_for_name_fields(self):
        user = User.objects.create_user(email="noname-stripe@test.com")

        # No ``name`` key at all.
        session = self._session(user)
        del session["customer_details"]["name"]
        handle_checkout_completed(session)

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")
        # Tier still updated.
        self.assertEqual(user.tier.slug, "basic")

    def test_new_user_created_by_webhook_gets_name(self):
        """Stripe Payment Link case: webhook creates the user AND fills name."""
        session = {
            "id": "cs_newuser_name",
            "customer": "cus_newuser_name",
            "customer_details": {
                "email": "brand-new-stripe@test.com",
                "name": "Brand New",
            },
            "subscription": "",
            "client_reference_id": None,
            "metadata": {"tier_slug": "basic"},
        }

        handle_checkout_completed(session)

        user = User.objects.get(email="brand-new-stripe@test.com")
        self.assertEqual(user.first_name, "Brand")
        self.assertEqual(user.last_name, "New")


@tag('core')
class StripeNameCaptureCoursePurchaseTest(TestCase):
    """``_handle_course_purchase`` populates names from Stripe."""

    def _course_session(self, course, user, *, name=""):
        return {
            "id": "cs_course_name",
            "customer": "cus_course_name",
            "customer_details": {"email": user.email, "name": name},
            "subscription": "",
            "client_reference_id": str(user.pk),
            "metadata": {"course_id": str(course.pk)},
        }

    def setUp(self):
        self.course = Course.objects.create(
            title="Resilient LLM Apps",
            slug="resilient-llm-apps-names",
            status="published",
            individual_price_eur=Decimal("99.00"),
        )

    def test_course_purchase_captures_name(self):
        user = User.objects.create_user(email="course-name@test.com")

        handle_checkout_completed(
            self._course_session(self.course, user, name="Alex Grigorev"),
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Alex")
        self.assertEqual(user.last_name, "Grigorev")
        # Underlying CourseAccess row was still created.
        self.assertTrue(
            CourseAccess.objects.filter(user=user, course=self.course).exists()
        )

    def test_course_purchase_does_not_overwrite_existing_name(self):
        user = User.objects.create_user(
            email="course-kept@test.com",
            first_name="Custom",
            last_name="Edit",
        )

        handle_checkout_completed(
            self._course_session(self.course, user, name="Wrong Name"),
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "Custom")
        self.assertEqual(user.last_name, "Edit")
        # CourseAccess still granted.
        self.assertTrue(
            CourseAccess.objects.filter(user=user, course=self.course).exists()
        )

    def test_course_purchase_with_no_name_still_creates_access(self):
        user = User.objects.create_user(email="course-noname@test.com")

        handle_checkout_completed(
            self._course_session(self.course, user, name=""),
        )

        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")
        self.assertTrue(
            CourseAccess.objects.filter(user=user, course=self.course).exists()
        )
