import io
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, override_settings
from PIL import Image

from integrations.services.article_images import (
    IMMUTABLE_CACHE_CONTROL,
    build_article_image_manifest,
)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs


class FailingS3(FakeS3):
    def put_object(self, **kwargs):
        raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "PutObject")


@override_settings(
    CONTENT_CDN_BASE="https://cdn.example.com",
    AWS_S3_CONTENT_BUCKET="content-bucket",
)
class ArticleImageVariantTest(SimpleTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source = SimpleNamespace(
            repo_name="AI-Shipping-Labs/content",
            short_name="content",
        )
        os.makedirs(os.path.join(self.temp_dir.name, "blog", "images"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _save(self, filename, *, size=(900, 600), mode="RGB", fmt="JPEG"):
        path = os.path.join(self.temp_dir.name, "blog", "images", filename)
        color = (30, 80, 160, 120) if mode == "RGBA" else (30, 80, 160)
        Image.new(mode, size, color).save(path, fmt)
        return path

    @patch("integrations.services.article_images.s3_content_upload_enabled", return_value=True)
    def test_deterministic_non_upscaled_variants_and_immutable_metadata(self, _enabled):
        self._save("cover.jpg")
        client = FakeS3()
        kwargs = {
            "source": self.source,
            "repo_dir": self.temp_dir.name,
            "rel_path": "blog/post.md",
            "body": "![Body](images/cover.jpg)",
            "cover_image": "images/cover.jpg",
            "client": client,
        }
        first, first_stats = build_article_image_manifest(**kwargs)
        second, second_stats = build_article_image_manifest(**kwargs)

        self.assertEqual(first, second)
        entry = first["https://cdn.example.com/content/blog/images/cover.jpg"]
        self.assertEqual((entry["width"], entry["height"]), (900, 600))
        self.assertEqual(
            [(item["width"], item["type"]) for item in entry["variants"]],
            [
                (320, "image/webp"),
                (320, "image/jpeg"),
                (480, "image/webp"),
                (480, "image/jpeg"),
                (768, "image/webp"),
                (768, "image/jpeg"),
            ],
        )
        self.assertEqual(first_stats.generated, 6)
        self.assertEqual(second_stats.reused, 6)
        self.assertTrue(first_stats.complete)
        self.assertTrue(second_stats.complete)
        for stored in client.objects.values():
            self.assertEqual(stored["CacheControl"], IMMUTABLE_CACHE_CONTROL)
            self.assertIn(stored["ContentType"], {"image/webp", "image/jpeg"})
            self.assertNotIn(b"Exif", stored["Body"])

    @patch("integrations.services.article_images.s3_content_upload_enabled", return_value=True)
    def test_png_alpha_preserved_and_external_never_opened(self, _enabled):
        self._save("alpha.png", size=(480, 320), mode="RGBA", fmt="PNG")
        client = FakeS3()
        manifest, stats = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path="blog/post.md",
            body=("![Alpha](images/alpha.png)\n![External](https://untrusted.example/image.jpg)"),
            client=client,
        )
        entry = manifest["https://cdn.example.com/content/blog/images/alpha.png"]
        self.assertEqual(entry["source_type"], "image/png")
        png = next(
            item
            for item in client.objects.values()
            if item["ContentType"] == "image/png" and item["Key"].endswith("/480.png")
        )
        with Image.open(io.BytesIO(png["Body"])) as decoded:
            self.assertEqual(decoded.mode, "RGBA")
        self.assertEqual(stats.skipped, 1)
        self.assertTrue(stats.complete)
        self.assertNotIn("https://untrusted.example/image.jpg", manifest)

    @patch("integrations.services.article_images.s3_content_upload_enabled", return_value=True)
    def test_corrupt_image_warns_without_aborting_valid_image(self, _enabled):
        self._save("good.jpg", size=(320, 200))
        with open(os.path.join(self.temp_dir.name, "blog", "images", "bad.jpg"), "wb") as bad:
            bad.write(b"not an image")
        manifest, stats = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path="blog/post.md",
            body="![Bad](images/bad.jpg)\n![Good](images/good.jpg)",
            client=FakeS3(),
        )
        self.assertEqual(stats.failed, 1)
        self.assertTrue(stats.complete)
        self.assertFalse(stats.errors[0]["retryable"])
        self.assertEqual(stats.errors[0]["step"], "article_image_variant")
        self.assertIn("good.jpg", next(iter(manifest)))

    @patch("integrations.services.article_images.s3_content_upload_enabled", return_value=True)
    def test_dry_run_does_not_write_objects(self, _enabled):
        self._save("cover.jpg", size=(320, 200))
        client = FakeS3()
        manifest, stats = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path="blog/post.md",
            body="",
            cover_image="images/cover.jpg",
            dry_run=True,
            client=client,
        )
        self.assertTrue(manifest)
        self.assertEqual(stats.generated, 2)
        self.assertEqual(client.objects, {})

    @patch('integrations.services.article_images.s3_content_upload_enabled', return_value=True)
    def test_exif_orientation_is_applied_to_manifest_and_output(self, _enabled):
        path = os.path.join(self.temp_dir.name, 'blog', 'images', 'oriented.jpg')
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (480, 320), 'red').save(path, 'JPEG', exif=exif)
        manifest, _ = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path='blog/post.md',
            body='![Oriented](images/oriented.jpg)',
            client=FakeS3(),
        )
        entry = next(iter(manifest.values()))
        self.assertEqual((entry['width'], entry['height']), (320, 480))

    @patch('integrations.services.article_images.MAX_SOURCE_PIXELS', 10)
    @patch('integrations.services.article_images.s3_content_upload_enabled', return_value=True)
    def test_resource_limit_and_animated_gif_fall_back(self, _enabled):
        self._save('large.jpg', size=(320, 200))
        gif_path = os.path.join(self.temp_dir.name, 'blog', 'images', 'animated.gif')
        frames = [Image.new('RGB', (20, 20), color) for color in ('red', 'blue')]
        frames[0].save(gif_path, save_all=True, append_images=frames[1:], format='GIF')
        manifest, stats = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path='blog/post.md',
            body='![Large](images/large.jpg)\n![GIF](images/animated.gif)',
            client=FakeS3(),
        )
        self.assertEqual(manifest, {})
        # GIF is rejected by decoded format/animation; the JPEG by resource cap.
        self.assertEqual(stats.failed, 2)
        self.assertTrue(stats.complete)

    @patch("integrations.services.article_images.s3_content_upload_enabled", return_value=True)
    def test_empty_terminal_matrix_is_complete_but_storage_failure_is_retryable(self, _enabled):
        with open(os.path.join(self.temp_dir.name, "blog", "images", "bad.jpg"), "wb") as bad:
            bad.write(b"not an image")
        gif_path = os.path.join(self.temp_dir.name, "blog", "images", "animated.gif")
        frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
        frames[0].save(gif_path, save_all=True, append_images=frames[1:], format="GIF")

        cases = {
            "coverless": "",
            "external": "![External](https://untrusted.example/image.jpg)",
            "corrupt": "![Corrupt](images/bad.jpg)",
            "animated": "![Animated](images/animated.gif)",
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                manifest, stats = build_article_image_manifest(
                    source=self.source,
                    repo_dir=self.temp_dir.name,
                    rel_path="blog/post.md",
                    body=body,
                    client=FakeS3(),
                )
                self.assertEqual(manifest, {})
                self.assertTrue(stats.complete)

        self._save("eligible.jpg", size=(320, 200))
        manifest, stats = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path="blog/post.md",
            body="![Eligible](images/eligible.jpg)",
            client=FailingS3(),
        )
        self.assertEqual(manifest, {})
        self.assertFalse(stats.complete)
        self.assertTrue(stats.errors[0]["retryable"])

    @override_settings(CONTENT_CDN_BASE="", AWS_S3_CONTENT_BUCKET="")
    def test_coverless_and_external_only_are_complete_without_storage_config(self):
        for label, body in {
            "coverless": "",
            "external": "![External](https://untrusted.example/image.jpg)",
        }.items():
            with self.subTest(label=label):
                manifest, stats = build_article_image_manifest(
                    source=self.source,
                    repo_dir=self.temp_dir.name,
                    rel_path="blog/post.md",
                    body=body,
                )
                self.assertEqual(manifest, {})
                self.assertTrue(stats.complete)

        self._save("eligible.jpg", size=(320, 200))
        _manifest, stats = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path="blog/post.md",
            body="![Eligible](images/eligible.jpg)",
        )
        self.assertFalse(stats.complete)

    @patch("integrations.services.article_images._open_source", side_effect=OSError("temporary read failure"))
    @patch("integrations.services.article_images.s3_content_upload_enabled", return_value=True)
    def test_transient_source_io_failure_remains_retryable(self, _enabled, _open_source):
        self._save("eligible.jpg", size=(320, 200))

        manifest, stats = build_article_image_manifest(
            source=self.source,
            repo_dir=self.temp_dir.name,
            rel_path="blog/post.md",
            body="![Eligible](images/eligible.jpg)",
            client=FakeS3(),
        )

        self.assertEqual(manifest, {})
        self.assertFalse(stats.complete)
        self.assertTrue(stats.errors[0]["retryable"])
