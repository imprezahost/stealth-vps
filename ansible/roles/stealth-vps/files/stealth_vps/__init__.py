"""stealth-vps shared Python package.

Lives at /usr/local/lib/stealth-vps/ on deployed hosts (see
`stealth_vps_python_pkg_dir` in defaults). Imported by:
  - the metrics updater (stealth-vps-metrics-update.py.j2)
  - the Telegram bot (stealth_vps_bot.py)
  - the s-vps CLI (v0.7+)

Pure stdlib by design. No third-party deps so the package can be
copied into any environment without venv resolution.
"""

import os
from typing import Optional

from .state import (  # noqa: F401
    load_users_index,
    save_users_index,
    label_valid,
    USERS_INDEX_PATH,
    RESERVED_LABEL_PREFIX,
)
from .threex_client import ThreeXUIClient  # noqa: F401
from .backends import UserBackend, ThreeXUIBackend  # noqa: F401
from .backends_headless import HeadlessBackend, ReloadCallback  # noqa: F401
from .reloader import (  # noqa: F401
    Reloader,
    ReloadError,
    render_xray_config,
    render_xray_config_text,
    render_hysteria_config,
    render_hysteria_config_text,
    load_state_file,
)
from .subscription import (  # noqa: F401
    render_subscription_txt,
    write_subscription_file,
    SUBSCRIPTION_DIR,
)
from .urivider import build_vless_uri, build_hysteria2_uri  # noqa: F401


# Default sentinel path the role uses for panel state. Tests and the
# migration tool override it via the `panel_state_path=` arg.
PANEL_STATE_PATH = "/etc/stealth-vps/panel.state.yml"


def select_backend(
    *,
    panel_state_path: str = PANEL_STATE_PATH,
    threex_client: Optional[ThreeXUIClient] = None,
    reality_remark: str = "stealth-vps-reality",
    reality_flow: str = "xtls-rprx-vision",
    users_index_path: str = USERS_INDEX_PATH,
    reloader: Optional[ReloadCallback] = None,
    default_hysteria_password: str = "",
) -> UserBackend:
    """Return the right `UserBackend` impl for this host.

    Selection rule (v0.7.0): if `panel_state_path` exists on disk, the
    panel is running → use `ThreeXUIBackend` (double-write panel + index).
    Otherwise → use `HeadlessBackend` (index-as-source-of-truth + reload).

    The bot and CLI call this once at startup. Operators flip modes by
    running the role with `stealth_vps_panel_enabled=false` (which the
    role-level mutex in tasks/main.yml enforces never-both); after the
    next `s-vps update`, panel.state.yml is gone and the next bot
    restart picks up `HeadlessBackend`.

    Tests pass an explicit path under tmp_path so this works headless
    on the CI runner without touching /etc.
    """
    if os.path.exists(panel_state_path):
        if threex_client is None:
            raise ValueError(
                "panel mode detected (panel.state.yml present) but no "
                "ThreeXUIClient was supplied — caller must construct one "
                "from panel.state.yml before calling select_backend()"
            )
        return ThreeXUIBackend(
            threex_client,
            reality_remark=reality_remark,
            reality_flow=reality_flow,
            users_index_path=users_index_path,
        )
    return HeadlessBackend(
        users_index_path=users_index_path,
        reloader=reloader,
        default_hysteria_password=default_hysteria_password,
    )
