# Contributing to gdrive-linux

First off, thanks for taking the time to contribute! 🎉

The following is a set of guidelines for contributing to gdrive-linux. These are mostly guidelines, not rules. Use your best judgment, and feel free to propose changes to this document.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Pull Request Process](#pull-request-process)
- [Commit Convention](#commit-convention)
- [Issue Reporting](#issue-reporting)
- [Feature Requests](#feature-requests)

## Code of Conduct

This project and everyone participating in it is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to nithinneeraj60@gmail.com.

## Getting Started

gdrive-linux is a native Linux Google Drive network drive using FUSE. It mounts your Google Drive as a virtual filesystem with pure streaming — no local storage.

To understand the architecture, read the [README](README.md) and explore the project structure:

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
└── main.py               # Application entry point
```

## Development Setup

### Prerequisites

- Python 3.8+
- FUSE (libfuse2)
- A Google Cloud project with the Drive API enabled

### Setup

1. Fork and clone the repository:

```bash
git clone https://github.com/your-username/gdrive-linux.git
cd gdrive-linux
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Set up OAuth credentials:

```bash
export GDRIVE_CLIENT_ID='your-client-id'
export GDRIVE_CLIENT_SECRET='your-client-secret'
```

4. Run the application:

```bash
python main.py
```

## Coding Standards

### Python Style

This project uses **Ruff** for both linting and formatting.

```bash
# Lint check
ruff check .

# Format check
ruff format --check .

# Auto-format
ruff format .
```

All code must pass `ruff check .` and `ruff format --check .` before being merged.

### Type Annotations

All new code should include type annotations. We use **mypy** for static type checking:

```bash
mypy .
```

> **Note:** Some third-party packages (PyQt6, googleapiclient, fusepy) lack stubs, so mypy will show import errors for these. These are acceptable as long as your code itself is properly typed.

### General Guidelines

- **Single responsibility** — each function/method should do one thing
- **Docstrings** — public functions and methods should have docstrings explaining what they do
- **Logging** — use the `logging` module, not `print()`. Use appropriate log levels (debug, info, warning, error)
- **Thread safety** — be mindful of shared state. Use locks when accessing shared data structures
- **No secrets in code** — OAuth credentials come from environment variables or are baked by CI, never hardcoded

## Pull Request Process

1. **Create an issue first** — discuss the change you want to make before writing code
2. **Fork the repo** and create your branch from `main`:

```bash
git checkout -b feature/your-feature-name
```

3. **Make your changes** following the coding standards above
4. **Run the checks** locally before committing:

```bash
ruff check .
ruff format --check .
mypy .
```

5. **Commit your changes** using a clear commit message (see [Commit Convention](#commit-convention))
6. **Push to your fork** and open a pull request against `main`
7. **Ensure CI passes** — the pull request workflow will run lint, type-check, and build verification automatically

### Pull Request Requirements

- All CI checks must pass (lint, format, mypy, build)
- New features should include relevant updates to documentation (README, etc.)
- Bug fixes should include a description of the bug and how it was fixed
- Changes that affect the user experience should be noted in the PR description

## Commit Convention

We encourage clear, descriptive commit messages:

```
<type>: <short summary>

<optional body>
```

**Types:**
- `feat` — new feature
- `fix` — bug fix
- `docs` — documentation only
- `style` — code style changes (formatting, etc.)
- `refactor` — code restructuring without functional changes
- `perf` — performance improvement
- `test` — adding or updating tests
- `chore` — build, CI, or tooling changes

**Example:**
```
feat: add disk cache for file chunks

Implements LRU-evicted on-disk caching of 4 MB file chunks to
reduce repeated API calls for frequently-accessed file regions.
```

## Issue Reporting

When reporting issues, please include:

- A clear, descriptive title
- Steps to reproduce the issue
- Expected behavior and actual behavior
- Your environment (distro, Python version, FUSE version)
- Any relevant logs (run with `--debug` if available)
- Screenshots if applicable

## Feature Requests

Feature requests are welcome! Please open an issue describing:

- The problem you're trying to solve
- How you envision the feature working
- Any alternatives you've considered

We prioritize features that maintain the project's core philosophy: zero-local-storage streaming, simplicity, and Linux-native integration.

## License

By contributing, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
