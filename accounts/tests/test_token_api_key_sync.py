"""Tests for Token to community_base APIKey synchronization."""

from community_base.api.models import APIKey
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token

User = get_user_model()


class TokenAPIKeySyncTest(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            email="staff_sync@example.com",
            password="testpass123",
            is_staff=True,
        )
        self.member_user = User.objects.create_user(
            email="member_sync@example.com",
            password="testpass123",
            is_staff=False,
        )

    def test_staff_token_creation_creates_api_key(self):
        token = Token.objects.create(user=self.staff_user, name="Staff CLI Key")
        api_key = APIKey.objects.filter(lookup_prefix=token.lookup_prefix).first()
        self.assertIsNotNone(api_key)
        self.assertEqual(api_key.user, self.staff_user)
        self.assertEqual(api_key.kind, APIKey.Kind.STAFF)
        self.assertEqual(api_key.scopes, ["*"])
        self.assertEqual(api_key.key_hash, token.key_hash)

        # Authenticate using the plaintext key via APIKey.authenticate
        authenticated = APIKey.authenticate(token.key)
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, api_key.pk)
        self.assertTrue(authenticated.allows(("settings.write",)))
        self.assertTrue(authenticated.allows(("settings.read",)))

    def test_staff_token_deletion_deletes_api_key(self):
        token = Token.objects.create(user=self.staff_user, name="Ephemeral Token")
        prefix = token.lookup_prefix
        self.assertTrue(APIKey.objects.filter(lookup_prefix=prefix).exists())

        token.delete()
        self.assertFalse(APIKey.objects.filter(lookup_prefix=prefix).exists())
        self.assertIsNone(APIKey.authenticate(token.key))
