"""Serialize course announcements for the staff API (issue #1942)."""

from api.serializers.datetime import isoformat_or_none


def serialize_course_announcement(announcement):
    author = announcement.author
    return {
        'id': announcement.pk,
        'course': announcement.course.slug,
        'title': announcement.title,
        'body': announcement.body,
        'author_email': getattr(author, 'email', None),
        'status': announcement.status,
        'published_at': isoformat_or_none(announcement.published_at),
        'email_queued_at': isoformat_or_none(announcement.email_queued_at),
        'created_at': isoformat_or_none(announcement.created_at),
        'updated_at': isoformat_or_none(announcement.updated_at),
    }
