"""AISL adapters around the shared questionnaire onboarding workflow."""

from community_base.questionnaires import onboarding as _shared_onboarding

from content.access import LEVEL_BASIC, get_user_level
from integrations.config import get_bool_config
from integrations.services import llm

# Keep the shared onboarding API and its historical private compatibility
# helpers available through the site's existing import path.
for _name in dir(_shared_onboarding):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_shared_onboarding, _name)

ONBOARDING_AI_FLAG = "ONBOARDING_AI_ENABLED"
ONBOARDING_AI_STREAMING_FLAG = "ONBOARDING_AI_STREAMING"
ONBOARDING_AI_DEADLINE_FLAG = "ONBOARDING_AI_DEADLINE_SECONDS"
ONBOARDING_AI_MAX_ATTEMPTS_FLAG = "ONBOARDING_AI_MAX_ATTEMPTS"


def ai_onboarding_available():
    """Gate the shared AI flow with AISL's editable IntegrationSetting."""
    return llm.is_enabled() and get_bool_config(ONBOARDING_AI_FLAG, default=True)


def ai_onboarding_streaming_enabled():
    """Respect the site's IntegrationSetting for token streaming."""
    return ai_onboarding_available() and get_bool_config(
        ONBOARDING_AI_STREAMING_FLAG,
        default=True,
    )


def can_access_onboarding(user):
    """Adapt the shared onboarding flow to AISL's paid-tier policy."""
    return get_user_level(user) >= LEVEL_BASIC
