---
name: Bug Report
about: Report a bug to help us improve gdrive-linux
title: "[Bug] "
labels: bug
assignees: ""
---

## Describe the Bug

A clear and concise description of what the bug is.

## Steps to Reproduce

1. Launch gdrive-linux with `...`
2. Open file manager at `~/Google Drive (...)` 
3. Click on `...`
4. See error

## Expected Behavior

What did you expect to happen?

## Actual Behavior

What actually happened? Include error messages, stack traces, or screenshots.

## Environment

- **Distribution:** (e.g., Ubuntu 24.04, Fedora 40, Arch Linux)
- **Desktop Environment:** (e.g., GNOME, KDE, XFCE, Sway)
- **Installation method:** (e.g., .deb package, AppImage, from source)
- **Version:** (output of `gdrive-linux --version`, or commit hash if from source)
- **FUSE version:** (output of `fusermount --version`)

## Logs

Run the application from a terminal and paste the relevant log output:

```bash
gdrive-linux 2>&1 | tail -50
```

## Additional Context

- Does the issue happen consistently or intermittently?
- Does it affect all files or specific ones?
- Have you tried clearing the cache (`~/.cache/gdrive-linux/`)?
