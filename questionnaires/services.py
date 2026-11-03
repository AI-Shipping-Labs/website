"""Compatibility import for shared questionnaire response services."""

import sys

from community_base.questionnaires import services as _shared_services

sys.modules[__name__] = _shared_services
setattr(sys.modules[__package__], "services", _shared_services)
