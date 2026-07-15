# Private S3 downloads

Download metadata is canonical in the configured GitHub content source. Binary assets live in a private S3 bucket with Block Public Access enabled; public pages contain only the stable AI Shipping Labs detail and delivery endpoints.

## Configuration

Configure these values in Studio → Settings. Runtime reads use `IntegrationSetting` and require no redeploy.

### `AWS_S3_DOWNLOADS_BUCKET`

Private bucket containing downloadable assets. The application role needs `s3:GetObject` for the `downloads/` prefix only.

### `AWS_S3_DOWNLOADS_REGION`

Bucket region. Default: `eu-central-1`.

### `DOWNLOAD_PRESIGNED_URL_TTL_SECONDS`

Lifetime of the authorized S3 redirect. Allowed range: 60–900 seconds; default: 300.

### `DOWNLOAD_DELIVERY_TOKEN_TTL_HOURS`

Lifetime of a mailbox delivery grant. Allowed range: 1–72 hours; default: 24. Grants are resource-scoped and redeem once.

## Content YAML

```yaml
content_id: 55555555-5555-5555-5555-555555555555
slug: ai-cheat-sheet
title: AI cheat sheet
description: A practical reference.
storage_key: downloads/ai-cheat-sheet.pdf
file_type: pdf
file_size_bytes: 245760
asset_mime_type: application/pdf
required_level: 0
tags: [agents, reference]
```

Publishable types are PDF, ZIP, slides (`.ppt` with `application/vnd.ms-powerpoint`; `.pptx` with the OOXML presentation MIME), notebook (`.ipynb`), and CSV. Executable or active-content formats are rejected. Legacy `other` rows remain in the database but fail closed; adding any new type requires an explicit reviewed extension/MIME allowlist entry. `storage_key` must start with `downloads/` and contain no empty, current-directory, parent-directory, encoded-traversal, backslash, or control-character segment.

Sync performs an S3 `HeadObject` check before publishing. If an existing source
entry becomes invalid or the object disappears, the row is unpublished and
marked not ready without discarding its metadata; correcting the source/object
and syncing again restores it. Delivery HEAD-checks again immediately before
presigning because an object can disappear after sync. Failed checks do not
consume one-time grants or increment successful-download counters.

## Rollout and audit

Run `python manage.py backfill_download_storage_keys` for a dry-run report. Add `--apply` only after reviewing it. The command maps canonical HTTPS URLs for the configured bucket and region, never arbitrary external URLs, and is idempotent. Rows reported as unresolved remain visible in Studio as `Needs migration` and fail closed at delivery.

Prefer a forward fix if rollout needs correction. Reversing the private-storage migration drops delivery-grant records and the backfilled storage-key/MIME columns; it cannot preserve those new values. Take a database snapshot before migration or reversal. Legacy `file_url`, publication state, and download counts remain available after a reverse migration.

## Troubleshooting

- `Needs migration`: add a valid private `storage_key`, supported type/MIME, and positive size in the content source, then sync.
- `temporarily unavailable`: verify bucket/region settings, IAM `GetObject`, the object key, and S3 availability.
- expired email link: request the resource again from its detail page.
