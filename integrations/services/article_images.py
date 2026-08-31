"""Deterministic responsive variants for repository-owned Article images."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
from dataclasses import dataclass, field

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from integrations.config import get_config, s3_content_upload_enabled
from integrations.services.github_sync.checkout import (
    MAX_IMAGE_SNAPSHOT_BYTES,
    active_checkout,
    checkout_is_file,
    checkout_read_bytes,
    checkout_scope,
    extract_authored_image_references,
)
from integrations.services.github_sync.media import (
    _repo_short,
    _resolve_image_path,
    rewrite_cover_image_url,
)

logger = logging.getLogger(__name__)

VARIANT_WIDTHS = (320, 480, 768, 1200, 1600)
WEBP_QUALITY = 82
JPEG_QUALITY = 85
MAX_SOURCE_BYTES = MAX_IMAGE_SNAPSHOT_BYTES
MAX_SOURCE_PIXELS = 40_000_000
MAX_SOURCE_DIMENSION = 12_000
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
SUPPORTED_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}

@dataclass
class VariantStats:
    generated: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)
    complete: bool = True


class ArticleImageError(ValueError):
    """A controlled image cannot safely be transformed."""


def _controlled_path(reference, *, repo_dir, base_dir):
    if reference.startswith(("http://", "https://", "data:", "//")):
        return None
    checkout = active_checkout()
    if checkout is not None:
        relative = checkout.authored_image_relative(
            reference, base_dir=base_dir,
        )
        if relative is None:
            return None
    else:
        relative = _resolve_image_path(reference, base_dir)
    candidate = os.path.join(repo_dir, relative)
    if checkout is None and (relative == '..' or relative.startswith(f'..{os.sep}')):
        return None
    return relative, candidate


def _public_original_url(reference, *, source, rel_path):
    if reference.startswith(("http://", "https://")):
        return reference
    return rewrite_cover_image_url(reference, source, rel_path)


def _open_source(source):
    source_bytes = (
        source if isinstance(source, bytes) else checkout_read_bytes(source)
    )
    size = len(source_bytes)
    if size > MAX_SOURCE_BYTES:
        raise ArticleImageError(f"source exceeds {MAX_SOURCE_BYTES} byte limit")
    try:
        image = Image.open(io.BytesIO(source_bytes))
    except (UnidentifiedImageError, ValueError) as exc:
        raise ArticleImageError("source is corrupt or unsupported") from exc
    # Preserve filesystem/open OSError for the caller: a transient read or
    # permission failure must remain retryable. Once Pillow has identified the
    # source, decode errors are deterministic for these repository bytes and
    # are classified as a terminal original-URL fallback.
    if max(image.size) > MAX_SOURCE_DIMENSION or image.width * image.height > MAX_SOURCE_PIXELS:
        raise ArticleImageError("source exceeds configured pixel/dimension limits")
    try:
        image.load()
    except (OSError, ValueError) as exc:
        raise ArticleImageError("source is corrupt or unsupported") from exc
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
        raise ArticleImageError("animated images are not transformed")
    if image.format not in SUPPORTED_FORMATS:
        raise ArticleImageError(f"unsupported decoded format: {image.format or 'unknown'}")
    source_format = image.format
    image = ImageOps.exif_transpose(image)
    image = _to_srgb(image)
    return image, source_format, size


def _to_srgb(image):
    """Apply embedded color profile where possible, retaining alpha."""
    icc = image.info.get("icc_profile")
    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    if icc:
        try:
            source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            output_mode = "RGBA" if has_alpha else "RGB"
            image = ImageCms.profileToProfile(
                image,
                source_profile,
                ImageCms.createProfile("sRGB"),
                outputMode=output_mode,
            )
        except (ImageCms.PyCMSError, OSError, ValueError):
            logger.warning("Invalid embedded image color profile; using decoded colors")
    if has_alpha:
        return image.convert("RGBA")
    return image.convert("RGB")


def _encode(image, output_format):
    output = io.BytesIO()
    if output_format == "WEBP":
        image.save(output, "WEBP", quality=WEBP_QUALITY, method=6, exact=True)
    elif output_format == "JPEG":
        if image.mode != "RGB":
            background = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                background.paste(image, mask=image.getchannel("A"))
            image = background
        image.save(output, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    elif output_format == "PNG":
        image.save(output, "PNG", optimize=True, compress_level=9)
    return output.getvalue()


def _s3_client():
    kwargs = {"region_name": get_config("AWS_S3_CONTENT_REGION", "eu-central-1")}
    access_key = get_config("AWS_ACCESS_KEY_ID")
    secret_key = get_config("AWS_SECRET_ACCESS_KEY")
    if access_key:
        kwargs["aws_access_key_id"] = access_key
    if secret_key:
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def _store_variant(*, client, bucket, key, data, content_type, dry_run=False):
    if client is None or not bucket:
        return "generated"
    try:
        client.head_object(Bucket=bucket, Key=key)
        return "reused"
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchKey", "NotFound"}:
            raise
    if not dry_run:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl=IMMUTABLE_CACHE_CONTROL,
        )
    return "generated"


def build_article_image_manifest(
    *,
    source,
    repo_dir,
    rel_path,
    body,
    cover_image="",
    dry_run=False,
    client=None,
):
    """Build and persist a deterministic manifest for controlled raster refs.

    Absolute references are intentionally ignored. Errors are isolated per
    image and returned as structured warnings for the sync log.
    """
    with checkout_scope(repo_dir, preload=True):
        return _build_article_image_manifest_from_checkout(
            source=source,
            repo_dir=repo_dir,
            rel_path=rel_path,
            body=body,
            cover_image=cover_image,
            dry_run=dry_run,
            client=client,
        )


def _build_article_image_manifest_from_checkout(
    *,
    source,
    repo_dir,
    rel_path,
    body,
    cover_image="",
    dry_run=False,
    client=None,
):
    stats = VariantStats()
    manifest = {}
    base_dir = os.path.dirname(rel_path)
    work_items = []
    for reference in extract_authored_image_references(
        body, str(cover_image or "").strip(),
    ):
        resolved = _controlled_path(reference, repo_dir=repo_dir, base_dir=base_dir)
        if resolved is None:
            stats.skipped += 1
            continue
        relative, path = resolved
        if not checkout_is_file(path):
            stats.skipped += 1
            continue
        work_items.append((reference, relative, path))

    # Coverless, external-only, and missing-file fallbacks are conclusively
    # reconciled without requiring storage configuration. An unchanged repo
    # cannot turn one of these into an eligible local raster.
    if not work_items:
        return manifest, stats

    cdn_base = (get_config("CONTENT_CDN_BASE", "") or "").rstrip("/")
    bucket = get_config("AWS_S3_CONTENT_BUCKET")
    storage_enabled = bool(cdn_base and bucket and s3_content_upload_enabled())
    if not cdn_base or (not storage_enabled and not getattr(settings, "TESTING", False)):
        stats.complete = False
        return manifest, stats
    if client is None and storage_enabled and not getattr(settings, "TESTING", False):
        try:
            client = _s3_client()
        except (BotoCoreError, ClientError) as exc:
            stats.failed += 1
            stats.complete = False
            stats.errors.append(
                {
                    "image": "",
                    "error": str(exc),
                    "step": "article_image_s3_client",
                    "retryable": True,
                }
            )
            return manifest, stats

    repo_short = _repo_short(source.repo_name)
    for reference, relative, path in work_items:
        original_url = _public_original_url(reference, source=source, rel_path=rel_path)
        try:
            source_bytes = checkout_read_bytes(
                path, max_bytes=MAX_SOURCE_BYTES + 1,
            )
            if len(source_bytes) > MAX_SOURCE_BYTES:
                raise ArticleImageError(f"source exceeds {MAX_SOURCE_BYTES} byte limit")
            source_hash = hashlib.sha256(source_bytes).hexdigest()
            image, source_format, _ = _open_source(source_bytes)
            source_ext, source_mime = SUPPORTED_FORMATS[source_format]
            variants = []
            for width in VARIANT_WIDTHS:
                if width > image.width:
                    continue
                height = max(1, round(image.height * width / image.width))
                resized = (
                    image
                    if width == image.width
                    else image.resize(
                        (width, height),
                        Image.Resampling.LANCZOS,
                    )
                )
                formats = [("WEBP", "webp", "image/webp")]
                if source_format != "WEBP":
                    formats.append((source_format, source_ext, source_mime))
                for output_format, ext, mime in formats:
                    payload = _encode(resized, output_format)
                    # A URL-safe 120-bit SHA-256 prefix keeps repeated srcsets
                    # compact while retaining ample collision resistance for a
                    # content bucket. The full digest remains in the manifest.
                    object_hash = base64.urlsafe_b64encode(bytes.fromhex(source_hash[:30])).decode().rstrip("=")
                    key = f"{repo_short}/_variants/articles/{object_hash}/{width}.{ext}"
                    status = _store_variant(
                        client=client,
                        bucket=bucket,
                        key=key,
                        data=payload,
                        content_type=mime,
                        dry_run=dry_run,
                    )
                    setattr(stats, status, getattr(stats, status) + 1)
                    base = cdn_base or "https://cdn.example.invalid"
                    variants.append({"url": f"{base}/{key}", "width": width, "height": height, "type": mime})
            if variants:
                manifest[original_url] = {
                    "width": image.width,
                    "height": image.height,
                    "source_hash": source_hash,
                    "source_type": source_mime,
                    "source_path": relative,
                    "variants": variants,
                }
            else:
                stats.skipped += 1
        except ArticleImageError as exc:
            stats.failed += 1
            stats.errors.append(
                {
                    "image": reference,
                    "error": str(exc),
                    "step": "article_image_variant",
                    "retryable": False,
                }
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            stats.failed += 1
            stats.complete = False
            stats.errors.append(
                {
                    "image": reference,
                    "error": str(exc),
                    "step": "article_image_variant",
                    "retryable": True,
                }
            )
    return manifest, stats
