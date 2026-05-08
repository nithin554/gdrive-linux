# gdrive-linux

**Native Linux Google Drive two-way synchronization application** with real-time file monitoring, system tray integration, and on-demand file content management.

## Features

- **Two-way sync** between local folder and Google Drive
- **Real-time monitoring** using `watchdog` with debounced file events
- **System tray icon** with context menu (open folder, manual sync, settings, exit)
- **On-demand sync** — placeholder files that download content only when accessed
- **Automatic rollback** — unused local files can revert to placeholders to save space
- **Periodic remote sync** — checks Google Drive for changes using the Changes API
- **OAuth 2.0 authentication** with Google account
- **Settings window** — view account status, log out, change sync folder
- **Conflict resolution** — when both local and remote change, remote wins with a local backup
- **XDG autostart** — optional start on login

## Installation

### From release

Download the package for your distribution from the [Releases](https://github.com/nithin/gdrive-linux/releases) page:

| Package | Distro | File |
|---------|--------|------|
| `.deb` | Debian, Ubuntu, Mint, Pop!_OS | `gdrive-linux-{version}.deb` |
| `.rpm` | Fedora, RHEL, CentOS | `gdrive-linux-{version}.rpm` |
| `.pkg.tar.zst` | Arch, Manjaro, EndeavourOS | `gdrive-linux-{version}.pkg.tar.zst` |
| `AppImage` | **All distros** (universal) | `gdrive-linux-{version}-x86_64.AppImage` |

**AppImage** requires no installation — just make it executable and run:
```bash
chmod +x gdrive-linux-*.AppImage
./gdrive-linux-*.AppImage
```

### From source

```bash
git clone https://github.com/nithin/gdrive-linux.git
cd gdrive-linux
pip install -r requirements.txt
python main.py
```

## First-time Setup

**If running from a pre-built package:** OAuth credentials are already bundled. Just run the application.

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

## Configuration

Auto-generated files in `~/.config/gdrive-linux/`:

| File | Purpose |
|------|---------|
| `token.json` | User's Google authentication token |
| `sync_mapping.json` | Local↔Drive file ID mapping |
| `settings.json` | User preferences (sync folder, etc.) |

Environment variables:

| Variable | Purpose |
|----------|---------|
| `GDRIVE_CLIENT_ID` | Google OAuth client ID |
| `GDRIVE_CLIENT_SECRET` | Google OAuth client secret |
| `GDRIVE_SYNC_FOLDER` | Override the local sync directory |

Edit `config.py` to customize defaults:
- `LOCAL_SYNC_FOLDER` — path to the local sync directory
- `DEFAULT_FILE_MODE` — `"local"` (download all content) or `"online"` (create placeholders)
- `REMOTE_SYNC_INTERVAL_SECONDS` — how often to check Drive for changes (default: 60s)
- `ROLLBACK_PERIOD_SECONDS` — time after which unused local files revert to placeholders (default: 1 hour)

## Project Structure

```
gdrive-linux/
├── main.py               # Application entry point
├── config.py             # Configuration constants
├── auth.py               # Google OAuth 2.0 authentication
├── sync_manager.py       # Core sync engine (Drive API operations)
├── watchdog_handler.py   # Local file system event handler
├── sync_threads.py       # Background threads (remote sync, rollback)
├── gui_elements.py       # GUI components (SettingsWindow, SystemTrayIcon)
├── requirements.txt      # Python dependencies
├── .github/              # CI/CD workflows and scripts
└── README.md
```

## Requirements

- Python 3.8+
- Google Drive API enabled (free tier)
- PyQt6 for system tray and GUI
