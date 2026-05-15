"""stealth-vps shared Python package.

Lives at /usr/local/lib/stealth-vps/ on deployed hosts (see
`stealth_vps_python_pkg_dir` in defaults). Imported by:
  - the metrics updater (stealth-vps-metrics-update.py.j2)
  - the Telegram bot (stealth_vps_bot.py)
  - the s-vps CLI (v0.7+)

Pure stdlib by design. No third-party deps so the package can be
copied into any environment without venv resolution.
"""

from .state import (  # noqa: F401
    load_users_index,
    save_users_index,
    label_valid,
    USERS_INDEX_PATH,
    RESERVED_LABEL_PREFIX,
)
from .threex_client import ThreeXUIClient  # noqa: F401
from .backends import UserBackend, ThreeXUIBackend  # noqa: F401
from .subscription import (  # noqa: F401
    render_subscription_txt,
    write_subscription_file,
    SUBSCRIPTION_DIR,
)
from .urivider import build_vless_uri, build_hysteria2_uri  # noqa: F401
