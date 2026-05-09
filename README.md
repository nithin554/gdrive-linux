# gdrive-linux

**Native Google Drive network drive for Linux** — mounts your Google Drive as a
FUSE virtual filesystem at `~/Google Drive (<email>)`. Pure streaming architecture:
zero local storage, files are streamed from Drive on read and uploaded on close.

[![Pull Request Checks](https://github.com/nithin554/gdrive-linux/actions/workflows/pull-request.yml/badge.svg)](https://github.com/nithin554/gdrive-linux/actions/workflows/pull-request.yml)
[![Build and Release](https://github.com/nithin554/gdrive-linux/actions/workflows/merge.yml/badge.svg)](https://github.com/nithin554/gdrive-linux/actions/workflows/merge.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Features

- **True network drive** — FUSE virtual filesystem mounted at `~/Google Drive (<email>)`, accessible to all apps
- **Pure streaming** — `read()` streams from Drive API into memory; `write()` buffers in memory, uploads on close. **Nothing stored on disk**
- **No placeholder files** — no cache files, no downloads, no rollback logic
- **Instant listing** — `ls` / file manager / `stat` are metadata-only, zero network
- **System tray icon** — context menu with open folder, settings, exit
- **OAuth 2.0 authentication** — secure Google Drive access
- **Periodic remote sync** — checks Drive for new/changed files every 60s
- **Read-write** — create, edit, rename files and folders normally
- **Delete protection** — delete operations are blocked with a notification to prevent accidental permanent deletion
- **FUSE mount verification** — detects stale mounts, leftover real directories, and failed mounts with user notification
- **Account switching** — log out and log in with a different Google account without restarting
- **Reset All Data** — clear cache, mapping, and authentication all at once from the tray menu or settings
- **Cross-distro packages** — `.deb`, `.rpm`, `.pkg.tar.zst`, and universal AppImage

## How It Works

gdrive-linux mounts a **FUSE virtual filesystem** at `~/Google Drive (<email>)` that presents
your Google Drive as a regular network drive — but **no file content is ever
stored on your disk**. Every `read()` streams data directly from the Drive API
over HTTP, and every `write()` is held in memory and uploaded to Drive when
the file is closed. The mount point includes your Google account email (e.g.
`~/Google Drive (user@gmail.com)`), so multiple accounts can coexist.

```
User / app reads file content
        │
        ▼
  FUSE intercepts read()          ← open()/stat() are free (zero network)
        │
        ▼
  FUSE streams from Drive API     ← content goes into memory (never disk)
        │
        ▼
  User edits file
        │
        ▼
  FUSE uploads to Drive on close  ← dirty buffer sent to Drive API
```

**Key benefits:**
- ✅ **Zero disk usage** — no cache files, no placeholders, no downloads ever
- ✅ **Instant listing** — files appear immediately with real sizes (metadata only)
- ✅ `ls` / file manager / `stat` do **not** trigger any network calls
- ✅ **Infinite storage** — your entire Drive is accessible with zero local footprint
- ✅ **Always fresh** — reads the latest version from Drive every time
- ✅ **No rollback needed** — nothing is stored to roll back
- ✅ **Delete-safe** — accidental deletions are blocked with a warning notification

## Installation

### From release

Download the package for your distribution from the [Releases page](https://github.com/nithin/gdrive-linux/releases).

| Package | Distro | Install command |
|---------|--------|----------------|
| `.deb` | Debian, Ubuntu, Mint, Pop!_OS | `sudo dpkg -i gdrive-linux-*.deb` |
| `.rpm` | Fedora, RHEL, CentOS | `sudo rpm -i gdrive-linux-*.rpm` |
| `.pkg.tar.zst` | Arch, Manjaro, EndeavourOS | `sudo pacman -U gdrive-linux-*.pkg.tar.zst` |
| `AppImage` | **All distros** | `chmod +x *.AppImage && ./gdrive-linux-*.AppImage` |

**Dependencies:** The application requires **FUSE** (libfuse2) to be installed:

```bash
# Debian/Ubuntu
sudo apt install libfuse2

# Fedora
sudo dnf install fuse-libs

# Arch
sudo pacman -S fuse2
```

> **Note:** The application uses `fusepy` which looks for `libfuse.so` via `ctypes.util.find_library('fuse')`.
> Some systems only have `libfuse.so.2` (e.g., `/usr/lib/x86_64-linux-gnu/libfuse.so.2`).
> If you get a "FUSE library not found" error, set the environment variable:
> ```bash
> export FUSE_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu/libfuse.so.2
> ```
> Find your `libfuse.so.2` path with: `locate libfuse.so.2` or `find /usr -name 'libfuse.so*'`

### From source

```bash
git clone https://github.com/nithin/gdrive-linux.git
cd gdrive-linux
pip install -r requirements.txt
python main.py
```

## First-time Setup

**If running from a pre-built package:** OAuth credentials are bundled. Just run the application.

**If building from source:** You need a Google Cloud project with the Drive API enabled.

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Enable the **Google Drive API** for your project
3. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID** → **Desktop application**
4. Copy your **Client ID** and **Client Secret**

Set them via environment variables before running:

```bash
export GDRIVE_CLIENT_ID='your-client-id'
export GDRIVE_CLIENT_SECRET='your-client-secret'
gdrive-linux
```

On first run, your browser will open for Google account authentication. A `token.json` file is auto-generated in `~/.config/gdrive-linux/`.

## Usage

1. **Launch the application** — it authenticates with Google Drive and mounts the network drive automatically
2. The application immediately:
   - Scans your Google Drive and builds a file index
   - Mounts the FUSE virtual filesystem at `~/Google Drive (<email>)`
   - Files appear instantly with real sizes — **zero bytes stored locally**
   - Open or read a file — content streams from Drive into memory on demand
   - File manager scans and `ls` commands do **not** trigger any network calls
   - Write to a file — changes are held in memory, uploaded to Drive on close
3. **Left-click the tray icon** to open the network drive in your file manager
4. **Right-click the tray icon** to:
   - View your signed-in Google account email
   - Open the network drive in your file manager
   - Open settings (where you can log out / switch accounts, trigger manual sync, toggle autostart, or reset all data)
   - Reset all data (clears cache, sync mapping, and authentication — logs you out)
   - Quit the application

## Configuration

Auto-generated files in `~/.config/gdrive-linux/`:

| File | Purpose |
|------|---------|
| `token.json` | User's Google OAuth token |
| `sync_mapping.json` | Local path ↔ Drive file ID mapping |
| `settings.json` | Application preferences (autostart, etc.) |

Cache directory at `~/.cache/gdrive-linux/cache/` stores recently accessed file chunks for fast re-reads — automatically managed with LRU eviction.

Environment variables:

| Variable | Purpose |
|----------|---------|
| `GDRIVE_CLIENT_ID` | Google OAuth client ID |
| `GDRIVE_CLIENT_SECRET` | Google OAuth client secret |

Edit `config.py` to customize defaults:
- `REMOTE_SYNC_INTERVAL_SECONDS` — how often to check Drive for changes (default: 60s)
- `CACHE_CHUNK_SIZE` — size of each disk cache chunk (default: 4 MB)
- `CACHE_SIZE_LIMIT` — max disk cache size (default: 2 GB)
- `READAHEAD_WINDOW_CHUNKS` — number of chunks to fetch on cache miss (default: 4, i.e. 16 MB)
- `MAX_CONCURRENT_FETCHES` — max concurrent Drive API read requests (default: 3)

## Project Structure

```
gdrive-linux/
├── main.py               # Application entry point (PyQt6, FUSE mount, auth)
├── config.py             # Configuration constants
├── auth.py               # Google OAuth 2.0 authentication
├── sync_manager.py       # Core sync engine (Drive API operations, mapping)
├── fuse_drive.py         # FUSE virtual filesystem (pure streaming network drive)
├── sync_threads.py       # Background remote sync thread
├── gui_elements.py       # GUI components (SettingsWindow, SystemTrayIcon)
├── autostart.py          # XDG autostart .desktop file management
├── disk_cache.py         # LRU disk cache for file chunks
├── drive_service_pool.py # Thread-local Drive service instances for parallel reads
├── watchdog_handler.py   # Deprecated — kept for reference
├── requirements.txt      # Python dependencies
├── icons/                # Application icons
├── .github/              # CI/CD workflows and packaging scripts
└── README.md
```

## Requirements

- Python 3.8+
- FUSE (libfuse2) — required for the virtual filesystem
- Google Drive API enabled (free tier)
- PyQt6 for system tray and GUI

## CI/CD

| Workflow | Description |
|----------|-------------|
| [Pull Request Checks](https://github.com/nithin/gdrive-linux/actions/workflows/pull-request.yml) | Lint, type-check, and build verification on PRs |
| [Build & Release](https://github.com/nithin/gdrive-linux/actions/workflows/merge.yml) | Tag, build, and publish releases on merge to `main` |

Each release produces separate artifacts per distribution:
- **Ubuntu** → `.deb`, `.rpm`, `.AppImage`
- **Arch Linux** → `.pkg.tar.zst`

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and pull request guidelines.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you agree to uphold this code.

## License

MIT
