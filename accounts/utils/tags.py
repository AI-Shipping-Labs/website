"""Canonical contact-tag normalization, indexed reads, and mutations."""

import re

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count

TAG_MUTATION_CHUNK_SIZE = 500


def normalize_tag(tag):
    """Normalize a single operator contact tag."""
    if not tag or not isinstance(tag, str):
        return ""
    tag = tag.strip().lower()
    tag = tag.replace(" ", "-").replace("_", "-")
    tag = re.sub(r"[^a-z0-9:-]", "", tag)
    tag = re.sub(r"-{2,}", "-", tag)
    tag = tag.strip("-")
    return tag


def normalize_tags(tags):
    """Normalize contact tags, removing duplicates and empty values."""
    if not tags or not isinstance(tags, list):
        return []
    seen = set()
    result = []
    for tag in tags:
        normalized = normalize_tag(tag)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _tag_models():
    """Return ``(User, ContactTag, MemberExtra)``.

    A3.2 (#1692) moved the authoritative contact-tag relation from
    ``User.contact_tags`` to ``accounts_ext.MemberExtra.contact_tags``. Every
    read below goes through ``MemberExtra``; writes additionally keep the
    legacy ``User.contact_tags`` relation in step for the expand window, so an
    old image rolling alongside the new one still sees the same tag set. The
    contract unit drops the legacy write and the field together.
    """
    return (
        get_user_model(),
        apps.get_model("accounts_ext", "ContactTag"),
        apps.get_model("accounts_ext", "MemberExtra"),
    )


def _delete_unused_contact_tags():
    _, ContactTag, _ = _tag_models()
    ContactTag.objects.filter(
        users__isnull=True,
        member_extras__isnull=True,
    ).delete()


def sync_contact_tags(user):
    """Make the user's contact-tag relations set-equal to normalized ``tags``."""
    _, ContactTag, MemberExtra = _tag_models()
    slugs = normalize_tags(user.tags)
    ContactTag.objects.bulk_create(
        [ContactTag(slug=slug) for slug in slugs],
        ignore_conflicts=True,
    )
    tag_rows = list(ContactTag.objects.filter(slug__in=slugs))
    MemberExtra.for_user(user).contact_tags.set(tag_rows)
    # Legacy half of the expand window; removed by the contract unit.
    user.contact_tags.set(tag_rows)
    _delete_unused_contact_tags()


def set_tags(user, tags):
    """Replace a user's tags while preserving normalized input order."""
    user.tags = normalize_tags(tags)
    user.save(update_fields=["tags"])
    return list(user.tags)


def add_tag(user, raw):
    """Append a normalized tag, idempotently, to JSON and the relation."""
    normalized = normalize_tag(raw)
    if not normalized:
        return ""
    current = list(user.tags or [])
    if normalized in current:
        return normalized
    current.append(normalized)
    set_tags(user, current)
    return normalized


def remove_tag(user, raw):
    """Remove a normalized tag, idempotently, from JSON and the relation."""
    normalized = normalize_tag(raw)
    if not normalized:
        return ""
    current = list(user.tags or [])
    if normalized not in current:
        return normalized
    current.remove(normalized)
    set_tags(user, current)
    return normalized


def list_all_tags():
    """Return sorted slugs that have at least one user relation."""
    _, ContactTag, _ = _tag_models()
    return list(
        ContactTag.objects.filter(member_extras__isnull=False)
        .order_by("slug")
        .values_list("slug", flat=True)
        .distinct()
    )


def tags_with_user_counts():
    """Return all in-use tag names and carrier counts in one query."""
    _, ContactTag, _ = _tag_models()
    rows = (
        ContactTag.objects.annotate(user_count=Count("member_extras", distinct=True))
        .filter(user_count__gt=0)
        .order_by("slug")
        .values("slug", "user_count")
    )
    return [
        {"name": row["slug"], "user_count": row["user_count"]}
        for row in rows
    ]


def count_users_with_tag(name):
    """Return the number of users carrying the normalized slug."""
    normalized = normalize_tag(name)
    if not normalized:
        return 0
    _, ContactTag, _ = _tag_models()
    return (
        ContactTag.objects.filter(slug=normalized)
        .annotate(user_count=Count("member_extras", distinct=True))
        .values_list("user_count", flat=True)
        .first()
        or 0
    )


def user_ids_with_exact_tag(name):
    """Return an indexed queryset of user ids carrying one exact slug."""
    normalized = normalize_tag(name)
    User, _, _ = _tag_models()
    if not normalized:
        return User.objects.none().values_list("pk", flat=True)
    return User.objects.filter(
        member_extra__contact_tags__slug=normalized,
    ).values_list("pk", flat=True)


def user_ids_matching_tag_search(search):
    """Return user ids whose relation-backed slugs contain ``search``."""
    normalized = normalize_tag(search)
    User, _, _ = _tag_models()
    if not normalized:
        return User.objects.none().values_list("pk", flat=True)
    return (
        User.objects.filter(member_extra__contact_tags__slug__icontains=normalized)
        .order_by()
        .values_list("pk", flat=True)
        .distinct()
    )


def _replace_slug(tags, old, new=None):
    replaced = []
    seen = set()
    for slug in tags:
        candidate = new if slug == old else slug
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        replaced.append(candidate)
    return replaced


def _ensure_member_extras(MemberExtra, user_ids):
    """Create any missing MemberExtra rows for the batch."""
    known = set(
        MemberExtra.objects.filter(user_id__in=user_ids).values_list("user_id", flat=True)
    )
    missing = [user_id for user_id in user_ids if user_id not in known]
    if missing:
        MemberExtra.objects.bulk_create(
            [MemberExtra(user_id=user_id) for user_id in missing],
            ignore_conflicts=True,
            batch_size=TAG_MUTATION_CHUNK_SIZE,
        )


def _matched_user_batch(User, tag, last_pk):
    return list(
        User.objects.select_for_update()
        .filter(member_extra__contact_tags=tag, pk__gt=last_pk)
        .only("pk", "tags")
        .order_by("pk")[:TAG_MUTATION_CHUNK_SIZE]
    )


def rename_tag(old, new):
    """Rename one slug across matching users in atomic 500-row chunks."""
    new_normalized = normalize_tag(new)
    if not new_normalized:
        raise ValueError("New tag name cannot be empty.")

    old_normalized = normalize_tag(old)
    if not old_normalized:
        return {"affected": 0, "old": "", "new": new_normalized}
    if old_normalized == new_normalized:
        return {
            "affected": 0,
            "old": old_normalized,
            "new": new_normalized,
        }

    User, ContactTag, MemberExtra = _tag_models()
    affected = 0
    with transaction.atomic():
        old_tag = ContactTag.objects.select_for_update().filter(
            slug=old_normalized,
        ).first()
        if old_tag is None:
            return {
                "affected": 0,
                "old": old_normalized,
                "new": new_normalized,
            }
        new_tag, _ = ContactTag.objects.get_or_create(slug=new_normalized)
        through = MemberExtra.contact_tags.through
        legacy_through = User.contact_tags.through
        last_pk = 0
        while True:
            batch = _matched_user_batch(User, old_tag, last_pk)
            if not batch:
                break
            last_pk = batch[-1].pk
            batch_ids = [user.pk for user in batch]
            for user in batch:
                user.tags = _replace_slug(
                    list(user.tags or []),
                    old_normalized,
                    new_normalized,
                )
            User.objects.bulk_update(
                batch,
                ["tags"],
                batch_size=TAG_MUTATION_CHUNK_SIZE,
            )
            _ensure_member_extras(MemberExtra, batch_ids)
            through.objects.filter(
                memberextra_id__in=batch_ids,
                contacttag_id=old_tag.pk,
            ).delete()
            through.objects.bulk_create(
                [
                    through(memberextra_id=user_id, contacttag_id=new_tag.pk)
                    for user_id in batch_ids
                ],
                ignore_conflicts=True,
                batch_size=TAG_MUTATION_CHUNK_SIZE,
            )
            # Legacy half of the expand window; removed by the contract unit.
            legacy_through.objects.filter(
                user_id__in=batch_ids,
                contacttag_id=old_tag.pk,
            ).delete()
            legacy_through.objects.bulk_create(
                [
                    legacy_through(user_id=user_id, contacttag_id=new_tag.pk)
                    for user_id in batch_ids
                ],
                ignore_conflicts=True,
                batch_size=TAG_MUTATION_CHUNK_SIZE,
            )
            affected += len(batch)
        _delete_unused_contact_tags()

    return {
        "affected": affected,
        "old": old_normalized,
        "new": new_normalized,
    }


def delete_tag(name):
    """Delete one slug from matching users in atomic 500-row chunks."""
    normalized = normalize_tag(name)
    if not normalized:
        return {"affected": 0, "name": ""}

    User, ContactTag, MemberExtra = _tag_models()
    affected = 0
    with transaction.atomic():
        tag = ContactTag.objects.select_for_update().filter(slug=normalized).first()
        if tag is None:
            return {"affected": 0, "name": normalized}
        through = MemberExtra.contact_tags.through
        legacy_through = User.contact_tags.through
        last_pk = 0
        while True:
            batch = _matched_user_batch(User, tag, last_pk)
            if not batch:
                break
            last_pk = batch[-1].pk
            batch_ids = [user.pk for user in batch]
            for user in batch:
                user.tags = _replace_slug(
                    list(user.tags or []),
                    normalized,
                )
            User.objects.bulk_update(
                batch,
                ["tags"],
                batch_size=TAG_MUTATION_CHUNK_SIZE,
            )
            through.objects.filter(
                memberextra_id__in=batch_ids,
                contacttag_id=tag.pk,
            ).delete()
            # Legacy half of the expand window; removed by the contract unit.
            legacy_through.objects.filter(
                user_id__in=batch_ids,
                contacttag_id=tag.pk,
            ).delete()
            affected += len(batch)
        _delete_unused_contact_tags()

    return {"affected": affected, "name": normalized}


__all__ = [
    "TAG_MUTATION_CHUNK_SIZE",
    "add_tag",
    "count_users_with_tag",
    "delete_tag",
    "list_all_tags",
    "normalize_tag",
    "normalize_tags",
    "remove_tag",
    "rename_tag",
    "set_tags",
    "sync_contact_tags",
    "tags_with_user_counts",
    "user_ids_matching_tag_search",
    "user_ids_with_exact_tag",
]
