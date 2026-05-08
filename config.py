import os
import logging
import json

# --- OAuth 2.0 Client Configuration ---
# Both CLIENT_ID and CLIENT_SECRET are injected from GitHub repository secrets
# during CI build. They have no defaults — the app will fail on startup with
# a clear error message if they're not set.
# See: https://developers.google.com/identity/protocols/oauth2/native-app

CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")

# --- Paths ---
_CONFIG_HOME = os.environ.get(
    "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
)
_APP_CONFIG_DIR = os.path.join(_CONFIG_HOME, "gdrive-linux")

TOKEN_FILE = os.path.join(_APP_CONFIG_DIR, "token.json")
MAPPING_FILE = os.path.join(_APP_CONFIG_DIR, "sync_mapping.json")
SETTINGS_FILE = os.path.join(_APP_CONFIG_DIR, "settings.json")

# --- Scopes ---
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# --- Sync Folder ---
_DEFAULT_SYNC_FOLDER = os.path.abspath(
    os.environ.get("GDRIVE_SYNC_FOLDER", "my_gdrive_sync_folder")
)


def _load_sync_folder() -> str:
    """Load the sync folder path from settings file, env var, or default."""
    # Environment variable takes highest priority
    env_folder = os.environ.get("GDRIVE_SYNC_FOLDER")
    if env_folder:
        return os.path.abspath(env_folder)
    # Then settings file
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                saved = data.get("sync_folder")
                if saved:
                    return os.path.abspath(saved)
        except (json.JSONDecodeError, OSError):
            pass
    # Fall back to default
    return os.path.abspath(_DEFAULT_SYNC_FOLDER)


def save_sync_folder(path: str) -> bool:
    """Persist the sync folder path to settings file."""
    try:
        settings = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                settings = json.load(f)
        settings["sync_folder"] = os.path.abspath(path)
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        return True
    except OSError as e:
        logging.error("Failed to save sync folder setting: %s", e)
        return False


LOCAL_SYNC_FOLDER = _load_sync_folder()

# --- Sync Intervals ---
REMOTE_SYNC_INTERVAL_SECONDS = 60
ROLLBACK_PERIOD_SECONDS = 3600  # 1 hour
ROLLBACK_CHECK_INTERVAL_SECONDS = 300  # 5 minutes

# --- Default Mode ---
DEFAULT_FILE_MODE = "online"  # "local" or "online" — "online" creates placeholders, user downloads on demand

# --- Ensure config dir exists ---
os.makedirs(_APP_CONFIG_DIR, exist_ok=True)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
