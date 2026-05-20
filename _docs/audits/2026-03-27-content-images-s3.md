# Content Images: S3 Storage

## Problem

Blog articles (and future courses/projects) contain images. During content sync, image paths in markdown/HTML are rewritten to point to `CONTENT_CDN_BASE`. In production, these images need to be served from S3 (optionally fronted by CloudFront).

## Architecture

```
Content repo (GitHub)          Django sync service              S3 bucket
┌──────────────────┐          ┌─────────────────────┐         ┌──────────────┐
│ blog/             │  clone   │                     │  upload  │              │
│   article.md      │ ──────► │ 1. Parse markdown    │ ──────► │ blog/images/ │
│   images/         │         │ 2. Find image files  │         │   foo.png    │
│     foo.png       │         │ 3. Upload to S3      │         │   bar.jpg    │
│     bar.jpg       │         │ 4. Rewrite URLs      │         │              │
└──────────────────┘          └─────────────────────┘         └──────┬───────┘
                                                                     │
                                                              CloudFront (optional)
                                                                     │
                                                              https://cdn.aishippinglabs.com/blog/images/foo.png
```

## AWS Resources Needed

### S3 Bucket

- Bucket name: `aishippinglabs-content` (or similar)
- Region: `eu-central-1` (same as other infra, or wherever is closest to users)
- Public access: blocked (serve via CloudFront or signed URLs)
- Versioning: optional (nice for rollback but not required)
- Lifecycle rules: none needed (content is managed by sync)

### Bucket Policy

If using CloudFront, allow CloudFront OAC to read:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "cloudfront.amazonaws.com"
      },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::aishippinglabs-content/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::ACCOUNT_ID:distribution/DISTRIBUTION_ID"
        }
      }
    }
  ]
}
```

If not using CloudFront, make the bucket publicly readable:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::aishippinglabs-content/*"
    }
  ]
}
```

### CloudFront Distribution (recommended)

- Origin: S3 bucket `aishippinglabs-content`
- Origin access: OAC (Origin Access Control)
- Alternate domain: `cdn.aishippinglabs.com` (or `content.aishippinglabs.com`)
- SSL certificate: ACM cert for the domain (us-east-1 for CloudFront)
- Cache policy: CachingOptimized (TTL 24h+, images rarely change)
- Price class: use all edge locations or restrict to US/EU to save cost
- Default root object: not needed

### IAM Policy for the App

The Django app needs permission to upload to the bucket. Add to the existing IAM user or create a new one:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::aishippinglabs-content/*"
    }
  ]
}
```

### DNS

If using CloudFront with a custom domain, add a CNAME:

```
cdn.aishippinglabs.com  CNAME  d1234567890.cloudfront.net
```

## Environment Variables

```bash
# S3 bucket for content images
AWS_S3_CONTENT_BUCKET=aishippinglabs-content
AWS_S3_CONTENT_REGION=eu-central-1

# CDN base URL (CloudFront domain or S3 public URL)
# This replaces the current CONTENT_CDN_BASE=/static/content-images
CONTENT_CDN_BASE=https://cdn.aishippinglabs.com
```

The existing `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are reused (same IAM user, just add the S3 policy above).

## What the Django Code Will Do (after infra is ready)

The sync service (`integrations/services/github.py`) needs these changes:

1. During sync, walk the cloned repo for image files (png, jpg, jpeg, gif, webp, svg)
2. Upload each image to S3 under a path matching the repo structure: `s3://aishippinglabs-content/blog/images/foo.png`
3. Skip upload if the file already exists and has the same size (avoid re-uploading unchanged images)
4. `rewrite_image_urls()` already rewrites paths to `{CONTENT_CDN_BASE}/content/images/...` — just set `CONTENT_CDN_BASE` to the CloudFront URL
5. No changes needed to templates or models

## S3 Key Structure

```
aishippinglabs-content/
  blog/
    images/
      2026-03-05-my-experiments-with-claude-code/
        image1.jpg
        image2.png
      ai-generated-website-final.png
  courses/
    aihero/
      images/
        ...
  projects/
    images/
      ...
```

Mirrors the content repo structure so paths are predictable.

## Summary of What to Create

1. S3 bucket `aishippinglabs-content` in `eu-central-1`
2. CloudFront distribution with OAC pointing to the bucket
3. ACM certificate for `cdn.aishippinglabs.com` (in us-east-1)
4. DNS CNAME `cdn.aishippinglabs.com` → CloudFront
5. Add S3 PutObject/DeleteObject to the app's IAM policy
6. Set `AWS_S3_CONTENT_BUCKET` and `CONTENT_CDN_BASE` env vars
