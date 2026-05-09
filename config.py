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

# Read strategy for sequential/seek-friendly access.
#
# STRATEGY: On cache miss, fetch a larger window around the requested
# position (default 32 MB = 8 chunks). This is fetched as a single HTTP
# Range request (no overlapping windows) and all contained 4 MB chunks
# are written to the disk cache. Subsequent reads within that window
# hit the cache instantly — zero latency for smooth video playback.
#
# Additionally, a background pre-fetch thread is triggered when the
# reader reaches the middle of a cached chunk. This pre-fetches the
# NEXT window before the player needs it, eliminating stutter in
# sequential playback.
#
# Multiple files can be fetched concurrently (up to MAX_CONCURRENT_FETCHES),
# but each file's window is fetched atomically. This prevents the old
# problem of overlapping 16 MB windows from concurrent seeks in the same
# file causing 50-80 MB/s bandwidth spikes.
#
# The window is aligned to chunk boundaries and clamped to file size.
# Set to 0 to disable windowed fetching (fetch only the needed chunk).
READAHEAD_WINDOW_CHUNKS = 8  # 8 * 4MB = 32 MB window

# Threshold (0.0-1.0) within a cached chunk that triggers background pre-fetch.
# 0.5 means: when the reader has consumed 50% of a chunk, start pre-fetching
# the next one. Higher values mean less aggressive pre-fetching.
PREFETCH_TRIGGER_THRESHOLD = 0.5

# Maximum number of concurrent Drive API window fetches.
# Each window fetch uses one slot. 3 allows smooth playback across
# 3 different files simultaneously (e.g., playing a video while
# viewing another).
MAX_CONCURRENT_FETCHES = 3

# --- Scopes ---
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# --- FUSE mount point (network drive) ---
# Dynamic path under the user's home directory. Includes the logged-in email
# so multiple accounts can be distinguished. Set dynamically in main.py after
# authentication. Do NOT use before auth completes.
FUSE_MOUNT_POINT = None  # Set by main.py in _init_auth_only()

# --- LOCAL_SYNC_FOLDER is kept for backward compatibility in the mapping.
# In pure streaming mode it's identical to FUSE_MOUNT_POINT.
LOCAL_SYNC_FOLDER = None  # Set to FUSE_MOUNT_POINT after auth

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
