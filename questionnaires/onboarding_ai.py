"""Compatibility import for shared onboarding prompt and validation logic."""

import sys

from community_base.questionnaires import onboarding_ai as _shared_onboarding_ai

sys.modules[__name__] = _shared_onboarding_ai
setattr(sys.modules[__package__], "onboarding_ai", _shared_onboarding_ai)
