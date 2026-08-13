"""Shared Maven course-email preference helpers."""

from integrations.models import MavenEnrollmentEvent

MAVEN_EMAILS_KEY = "maven_emails"


def maven_email_preference(user):
    """Return the persisted preference, defaulting to on when absent."""
    preferences = user.email_preferences
    if not isinstance(preferences, dict):
        preferences = {}
    return preferences.get(MAVEN_EMAILS_KEY, True)


def is_maven_relevant(user):
    """Whether Maven-specific consent controls apply to ``user``."""
    preferences = user.email_preferences
    if isinstance(preferences, dict) and MAVEN_EMAILS_KEY in preferences:
        return True
    return MavenEnrollmentEvent.objects.filter(user_id=user.pk).exists()


def set_maven_email_preference(user, enabled):
    """Persist only the Maven preference and return its previous value."""
    if not isinstance(enabled, bool):
        raise ValueError("maven_emails must be a boolean")

    preferences = user.email_preferences
    if not isinstance(preferences, dict):
        preferences = {}
    else:
        preferences = dict(preferences)

    previous = preferences.get(MAVEN_EMAILS_KEY, True)
    preferences[MAVEN_EMAILS_KEY] = enabled
    user.email_preferences = preferences
    user.save(update_fields=["email_preferences"])
    return previous
