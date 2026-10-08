"""Windows integrations, registered explicitly."""
from .audio import Audio
from .power import Power
from .status import Status
from .user import User

INTEGRATION_TYPES = (Audio, Power, Status, User)
