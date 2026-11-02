from django.test import SimpleTestCase

from content.models import Article, MarketingPage, Workshop
from content.models.mixins import PreviewTokenMixin


class PreviewTokenMixinOwnershipTest(SimpleTestCase):
    def test_previewable_models_share_the_mixin_implementation(self):
        for model in (Article, MarketingPage, Workshop):
            with self.subTest(model=model):
                self.assertIs(
                    model.regenerate_preview_token,
                    PreviewTokenMixin.regenerate_preview_token,
                )
