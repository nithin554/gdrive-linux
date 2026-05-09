"""
gdrive-linux: Native Linux Google Drive network drive.

Mounts a FUSE virtual filesystem at ~/Gdrive that presents Google Drive
as a pure streaming network drive — zero local storage, reads stream
from the Drive API, writes upload on close.
"""

import os
import sys
import time
import logging
import threading

from PyQt6.QtWidgets import QApplication, QMessageBox

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    FUSE_MOUNT_POINT,
    REMOTE_SYNC_INTERVAL_SECONDS,
)
from auth import authenticate_google_drive
from sync_manager import SyncManager
from fuse_drive import DriveFS
from sync_threads import RemoteSyncThread
from gui_elements import SettingsWindow, SystemTrayIcon


def _ensure_fuse_library() -> None:
    """Ensure the FUSE shared library can be found by fusepy.

    fusepy looks for ``libfuse.so`` via ``ctypes.util.find_library('fuse')``,
    but many systems ship only ``libfuse.so.2`` without a ``libfuse.so`` symlink.
    This function searches for a working libfuse and sets the
    ``FUSE_LIBRARY_PATH`` environment variable so fusepy can load it.

    It prefers the library version matching the available ``fusermount``
    binary (fuse3 over fuse2), since mixing versions causes mount failures.
    """
    if os.environ.get("FUSE_LIBRARY_PATH"):
        return

    try:
        from ctypes.util import find_library

        found = find_library("fuse")
    except Exception:
        found = None

    if found and os.path.isfile(found):
        return

    # Determine which fusermount is available to match library version
    from shutil import which

    has_fusermount3 = which("fusermount3") is not None

    # On systems with fusermount3, prefer fuse3 libraries (libfuse3.so.*)
    # Otherwise fall back to fuse2 (libfuse.so.2)
    if has_fusermount3:
        candidates = ["libfuse3.so", "libfuse3.so.4", "libfuse.so.2"]
    else:
        candidates = ["libfuse.so.2", "libfuse3.so", "libfuse3.so.4"]

    search_dirs = [
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib/i386-linux-gnu",
        "/usr/lib/aarch64-linux-gnu",
        "/usr/lib/arm-linux-gnueabihf",
        "/usr/lib64",
        "/usr/lib",
        "/lib/x86_64-linux-gnu",
        "/lib",
    ]

    for directory in search_dirs:
        for name in candidates:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                os.environ["FUSE_LIBRARY_PATH"] = path
                logging.info(
                    "Auto-detected FUSE library: %s (fusermount=%s, fuse3=%s)",
                    path,
                    "fusermount3" if has_fusermount3 else "fusermount",
                    "yes" if has_fusermount3 else "no",
                )
                return

    logging.error(
        "FUSE shared library not found. "
        "Install libfuse2 (e.g., 'sudo apt install libfuse2') "
        "or set FUSE_LIBRARY_PATH to the full path of libfuse.so.2 / libfuse3.so. "
        "Run: locate libfuse.so.2"
    )


_ensure_fuse_library()


class SyncApp(QApplication):
    """Main application class for gdrive-linux."""

    def __init__(self, sys_argv):
        super().__init__(sys_argv)
        self.setQuitOnLastWindowClosed(False)

        self.drive_service = None
        self.sync_manager = None
        self._fuse_thread = None
        self._fuse_connection = None
        self.remote_sync_thread = None
        self.stop_event = threading.Event()
        self.settings_window = None
        self.tray_icon = None
        self._sync_started = False

        self._init_auth_only()
        self._init_tray_icon()
        self.start_sync()

    def _init_auth_only(self):
        """Authenticate with Google Drive without starting sync."""
        creds = authenticate_google_drive()
        if not creds:
            logging.error("Failed to authenticate with Google Drive. Exiting.")
            QMessageBox.critical(
                None,
                "Authentication Failed",
                "Could not authenticate with Google Drive. "
                "Please check your internet connection and restart the application.",
            )
            self.quit()
            return

        try:
            self.drive_service = build("drive", "v3", credentials=creds)
            logging.info("Google Drive service initialized.")
        except HttpError as error:
            logging.error(f"An error occurred while building Drive service: {error}")
            QMessageBox.critical(
                None,
                "Drive Service Error",
                f"Could not initialize Google Drive service: {error}",
            )
            self.quit()
            return

    def _mount_fuse(self):
        """Mount the FUSE filesystem in a background thread.

        Verifies the mount succeeded by checking if the mount point is
        actually a FUSE filesystem after starting the thread. If FUSE
        fails to mount (e.g., fusermount not found, stale mount, or
        permission error), the error is logged and a tray notification
        is shown rather than silently pretending the drive is available.
        """
        mount_point = FUSE_MOUNT_POINT

        # Ensure the mount point directory exists (as a regular directory)
        if not os.path.exists(mount_point):
            os.makedirs(mount_point)
            logging.info("Created FUSE mount point: %s", mount_point)

        # Check if mount point has leftover real directories from a previous
        # failed FUSE mount. These are NOT FUSE-served — they're real ext4 dirs.
        if os.path.isdir(mount_point) and not os.path.ismount(mount_point):
            leftover_items = os.listdir(mount_point)
            if leftover_items:
                logging.warning(
                    "Mount point %s contains %d leftover items from a previous "
                    "failed FUSE mount. These are real directories on disk, not "
                    "your Google Drive files. FUSE will mount over them.",
                    mount_point,
                    len(leftover_items),
                )
                self._on_tray_message(
                    f"~/Gdrive contains {len(leftover_items)} leftover items from a "
                    "previous failed mount. FUSE will mount over them now."
                )

        # Check for stale FUSE mounts before attempting to mount
        if os.path.ismount(mount_point):
            logging.warning(
                "Mount point %s is already a mount. Attempting to unmount first.",
                mount_point,
            )
            self._unmount_fuse()
            # Small delay to let the unmount settle
            time.sleep(0.5)

        # Create DriveFS with a notification callback for delete warnings
        def _notify(msg: str):
            logging.warning("FUSE notification: %s", msg)
            self._on_tray_message(msg)

        fs = DriveFS(
            self.sync_manager, notify_callback=_notify, stop_event=self.stop_event
        )

        # Use an Event to signal mount success/failure from the FUSE thread.
        # This avoids a race-prone time.sleep() + ismount() check.
        mount_event = threading.Event()
        fuse_error = []

        def _run_fuse():
            try:
                from fuse import FUSE as _FUSE

                _FUSE(
                    fs,
                    mount_point,
                    foreground=True,
                    allow_other=False,
                    nonempty=True,
                )
            except Exception as e:
                fuse_error.append(e)
                logging.error("FUSE mount failed: %s", e)
            finally:
                mount_event.set()

        self._fuse_thread = threading.Thread(target=_run_fuse, daemon=True)
        self._fuse_thread.start()

        # Wait for the FUSE thread to either complete (error) or mount.
        # fusepy's FUSE() constructor blocks until mount succeeds or fails,
        # so once mount_event is set, we have a definitive answer.
        mount_event.wait(timeout=10.0)

        if fuse_error:
            error_msg = str(fuse_error[0])
            logging.error("FUSE mount error: %s", error_msg)
            self._on_tray_message(
                f"FUSE mount failed: {error_msg[:100]}. "
                "Check that libfuse2 is installed and ~/Gdrive is not in use."
            )
            return False

        # Verify the mount actually took effect
        if not os.path.ismount(mount_point):
            logging.error(
                "FUSE thread signalled success but %s is not a mount point. "
                "FUSE may have failed silently.",
                mount_point,
            )
            self._on_tray_message(
                "FUSE mount failed silently. Try running: fusermount -uz ~/Gdrive"
            )
            return False

        logging.info("FUSE filesystem mounted at %s (verified).", mount_point)
        return True

    def _unmount_fuse(self):
        """Unmount the FUSE filesystem.

        Tries a standard unmount first, then falls back to lazy unmount
        (``fusermount -uz``) if the device is busy — for example when the
        FUSE thread hasn't fully exited yet.
        """
        mount_point = FUSE_MOUNT_POINT
        if not os.path.ismount(mount_point):
            return

        try:
            import subprocess

            # Try standard unmount
            result = subprocess.run(
                ["fusermount", "-u", mount_point],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logging.info("FUSE filesystem unmounted from %s.", mount_point)
                return

            # If busy, try lazy unmount
            if (
                "busy" in result.stderr.lower()
                or "device or resource busy" in result.stderr.lower()
            ):
                logging.warning(
                    "Standard unmount failed (%s). Trying lazy unmount...",
                    result.stderr.strip(),
                )
                result = subprocess.run(
                    ["fusermount", "-uz", mount_point],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    logging.info(
                        "FUSE filesystem lazily unmounted from %s.", mount_point
                    )
                else:
                    logging.warning(
                        "Lazy unmount also failed: %s", result.stderr.strip()
                    )
            else:
                logging.warning(
                    "fusermount -u returned %d: %s",
                    result.returncode,
                    result.stderr.strip(),
                )
        except FileNotFoundError:
            logging.warning(
                "fusermount not found; cannot unmount FUSE cleanly. "
                "The mount will be cleaned up on process exit."
            )
        except Exception as e:
            logging.error("Error unmounting FUSE: %s", e)

    def start_sync(self):
        """Start the Google Drive network drive."""
        if self._sync_started:
            logging.info("Sync already started.")
            return
        if not self.drive_service:
            logging.error("Cannot start sync: Drive service not initialized.")
            return

        # 1. Ensure the mount point directory exists
        if not os.path.exists(FUSE_MOUNT_POINT):
            os.makedirs(FUSE_MOUNT_POINT)
            logging.info("Created FUSE mount point: %s", FUSE_MOUNT_POINT)

        # 2. Initialize SyncManager
        self.sync_manager = SyncManager(self.drive_service)

        # 3. Check if mapping is stale (e.g., from a previous sync folder)
        if self.sync_manager.is_mapping_for_other_folder():
            logging.warning(
                "Mapping references a different path than %s. "
                "Re-initializing with fresh mapping.",
                FUSE_MOUNT_POINT,
            )
            self.sync_manager.initial_sync_from_drive()
        elif not self.sync_manager.last_change_token:
            self.sync_manager.initial_sync_from_drive()
        else:
            logging.info(
                "Skipping initial sync. last_change_token found. "
                "Will check for remote changes."
            )

        # 4. Mount FUSE filesystem
        if not self._mount_fuse():
            logging.error(
                "FUSE mount failed. Sync cannot proceed without the virtual filesystem."
            )
            self._on_tray_message(
                f"Failed to mount FUSE at {FUSE_MOUNT_POINT}. "
                "Check that libfuse2 is installed."
            )
            return

        # 5. Start remote sync thread
        self.remote_sync_thread = RemoteSyncThread(self.sync_manager, self.stop_event)
        self.remote_sync_thread.sync_status_signal.connect(self._on_tray_message)
        self.remote_sync_thread.start()
        logging.info(
            "Started remote sync thread, checking every %s seconds.",
            REMOTE_SYNC_INTERVAL_SECONDS,
        )

        self._sync_started = True
        self._on_tray_message(f"Google Drive mounted at {FUSE_MOUNT_POINT}")

    def stop_sync(self):
        """Stop all sync components."""
        if not self._sync_started:
            return

        logging.info("Stopping sync...")
        self.stop_event.set()

        self._unmount_fuse()

        if self.remote_sync_thread and self.remote_sync_thread.isRunning():
            self.remote_sync_thread.quit()
            self.remote_sync_thread.wait()

        self._sync_started = False
        self.stop_event.clear()
        logging.info("Sync stopped.")

    def _init_tray_icon(self):
        """Initialize the system tray icon with context menu."""
        self.tray_icon = SystemTrayIcon(sync_app=self)

    def _on_tray_message(self, message):
        """Show a tray notification."""
        if self.tray_icon:
            self.tray_icon._show_tray_message(message)

    def open_sync_folder(self):
        """Open the mount point in the file manager."""
        self.tray_icon._open_sync_folder()

    def manual_sync(self):
        """Trigger a manual sync from Drive."""
        if not self._sync_started:
            self._on_tray_message("Sync not active.")
            return
        if self.sync_manager:
            logging.info("Manual sync triggered.")
            self._on_tray_message("Manual sync started...")
            threading.Thread(
                target=self.sync_manager.sync_from_remote, daemon=True
            ).start()
            self._on_tray_message("Manual sync initiated.")
        else:
            self._on_tray_message("Sync manager not initialized.")

    def show_settings(self):
        """Show the settings window."""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
            self.settings_window.destroyed.connect(self._on_settings_closed)
        self.settings_window.show()
        self.settings_window.activateWindow()

    def _on_settings_closed(self):
        """Reset settings_window reference when the window is destroyed."""
        self.settings_window = None

    def quit_app(self):
        """Gracefully shut down all components and quit."""
        logging.info("Quitting application...")
        self.stop_sync()

        if self.tray_icon:
            self.tray_icon.hide()

        super().quit()


if __name__ == "__main__":
    app = SyncApp(sys.argv)
    sys.exit(app.exec())
