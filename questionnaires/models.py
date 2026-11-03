"""Compatibility import for the shared questionnaire domain models."""

import sys

from community_base.questionnaires import models as _shared_models

# Keep historical ``questionnaires.models`` imports bound to the shared module.
# The shared models declare ``app_label = "questionnaires"`` so this site keeps
# its existing migration graph and database tables.
sys.modules[__name__] = _shared_models
setattr(sys.modules[__package__], "models", _shared_models)
