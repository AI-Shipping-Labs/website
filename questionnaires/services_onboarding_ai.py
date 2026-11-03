"""Compatibility import for the shared onboarding turn state machine."""

import sys

from community_base.questionnaires import services_onboarding_ai as _shared_services

sys.modules[__name__] = _shared_services
setattr(sys.modules[__package__], "services_onboarding_ai", _shared_services)
