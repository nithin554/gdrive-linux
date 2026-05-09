import os
import logging

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

# --- Cache directory ---
_CACHE_HOME = os.environ.get(
    "XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")
)
FUSE_CACHE_DIR = os.path.join(_CACHE_HOME, "gdrive-linux", "fuse")

# --- Local disk cache settings ---
# Google Drive file chunks are cached on disk for performance. The cache uses
# an LRU eviction policy and a background cleanup thread periodically removes
# stale entries.
CACHE_DIR = os.path.join(_CACHE_HOME, "gdrive-linux", "cache")
CACHE_MAX_SIZE_MB = 1024  # Maximum disk cache size in MB (default 1 GB)
CACHE_MAX_AGE_SECONDS = 86400  # Evict files untouched for 24 hours
CACHE_CLEANUP_INTERVAL = 300  # Run LRU cleanup every 5 minutes
CACHE_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB chunks stored as separate files

# --- Scopes ---
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# --- FUSE mount point (network drive) ---
# Fixed path under the user's home directory. This is where the virtual
# filesystem is mounted — no user selection, works like /media/gdrive.
FUSE_MOUNT_POINT = os.path.join(os.path.expanduser("~"), "Gdrive")

# --- LOCAL_SYNC_FOLDER is kept for backward compatibility in the mapping.
# In pure streaming mode it's identical to FUSE_MOUNT_POINT.
LOCAL_SYNC_FOLDER = FUSE_MOUNT_POINT

# --- Sync Intervals ---
REMOTE_SYNC_INTERVAL_SECONDS = 60

# --- Default Mode ---
DEFAULT_FILE_MODE = "online"

# --- Ensure config dir exists ---
os.makedirs(_APP_CONFIG_DIR, exist_ok=True)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
