from . import auth
from . import client
from . import debug
from . import orders
from . import streaming
from . import utils

from .version import version as __version__

LOG_REDACTOR = debug.LogRedactor()

# The dependencies' loggers exist once the imports above have run.
from . import _redact  # noqa: E402
_redact.protect_dependencies()
