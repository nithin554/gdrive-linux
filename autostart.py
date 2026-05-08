"""XDG autostart support for gdrive-linux.

Creates/removes a .desktop file in ~/.config/autostart/ so the
application starts automatically on login.
"""

import os
import logging

AUTOSTART_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "autostart"
)
DESKTOP_FILE = os.path.join(AUTOSTART_DIR, "gdrive-linux.desktop")

DESKTOP_CONTENT = """[Desktop Entry]
Type=Application
Name=gdrive-linux
Comment=Google Drive two-way sync for Linux
Exec=gdrive-linux
Icon=gdrive-linux
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
"""


def is_autostart_enabled() -> bool:
    """Return True if gdrive-linux autostart .desktop file exists."""
    return os.path.isfile(DESKTOP_FILE)


def enable_autostart() -> bool:
    """Create the autostart .desktop file."""
    try:
        os.makedirs(AUTOSTART_DIR, exist_ok=True)
        with open(DESKTOP_FILE, "w") as f:
            f.write(DESKTOP_CONTENT)
        logging.info("Autostart enabled: %s", DESKTOP_FILE)
        return True
    except OSError as e:
        logging.error("Failed to enable autostart: %s", e)
        return False


def disable_autostart() -> bool:
    """Remove the autostart .desktop file."""
    try:
        if os.path.isfile(DESKTOP_FILE):
            os.remove(DESKTOP_FILE)
            logging.info("Autostart disabled: %s", DESKTOP_FILE)
            return True
        return False
    except OSError as e:
        logging.error("Failed to disable autostart: %s", e)
        return False
