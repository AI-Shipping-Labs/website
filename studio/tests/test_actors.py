from types import SimpleNamespace

from django.test import SimpleTestCase

from studio.actors import studio_actor_label


class StudioActorLabelTest(SimpleTestCase):
    def test_actor_label_uses_the_request_user_email(self):
        request = SimpleNamespace(
            user=SimpleNamespace(email="operator@example.test"),
        )

        self.assertEqual(
            studio_actor_label(request),
            "studio:operator@example.test",
        )
