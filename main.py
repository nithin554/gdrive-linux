"""
gdrive-linux: Native Linux Google Drive two-way sync application.

Entry point for the application. Initializes all components:
- Google Drive authentication
- System tray GUI
- Sync components (SyncManager, watchdog, sync threads) are started
  only after the user selects a sync folder via Settings.
"""

import os
import sys
import logging
import threading

from PyQt6.QtWidgets import QApplication, QMessageBox

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from watchdog.observers import Observer

from config import (
    LOCAL_SYNC_FOLDER,
    REMOTE_SYNC_INTERVAL_SECONDS,
    ROLLBACK_CHECK_INTERVAL_SECONDS,
    SETTINGS_FILE,
)
from auth import authenticate_google_drive
from sync_manager import SyncManager
from watchdog_handler import DriveSyncEventHandler
from sync_threads import RemoteSyncThread, RollbackThread
from gui_elements import SettingsWindow, SystemTrayIcon


class SyncApp(QApplication):
    """Main application class for gdrive-linux."""

    def __init__(self, sys_argv):
        super().__init__(sys_argv)
        self.setQuitOnLastWindowClosed(
            False
        )  # Don't quit when settings window is closed

        self.drive_service = None
        self.sync_manager = None
        self.observer = None
        self.remote_sync_thread = None
        self.rollback_thread = None
        self.stop_event = threading.Event()
        self.settings_window = None
        self.tray_icon = None
        self._sync_started = False

        self._init_auth_only()
        self._init_tray_icon()

        # If a sync folder is already configured, start sync immediately
        if os.path.exists(SETTINGS_FILE) and os.path.isdir(LOCAL_SYNC_FOLDER):
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

    def start_sync(self, fresh: bool = False):
        """Start synchronization after the sync folder is selected.

        Args:
            fresh: If True, force a fresh initial sync from Drive even if
                   a mapping already exists (used when folder changes).
        """
        if self._sync_started:
            logging.info("Sync already started.")
            return
        if not self.drive_service:
            logging.error("Cannot start sync: Drive service not initialized.")
            return

        # 1. Ensure local sync folder exists
        if not os.path.exists(LOCAL_SYNC_FOLDER):
            os.makedirs(LOCAL_SYNC_FOLDER)
            logging.info(f"Created local sync folder: {LOCAL_SYNC_FOLDER}")

        # 2. Initialize SyncManager
        self.sync_manager = SyncManager(self.drive_service)

        # 3. Detect if the mapping is stale (mismatched sync folder)
        mapping_stale = self.sync_manager.is_mapping_for_other_folder()
        if mapping_stale:
            logging.warning(
                "Mapping references a different folder than %s. "
                "Re-initializing sync with fresh mapping.",
                LOCAL_SYNC_FOLDER,
            )

        # 4. Perform initial sync from Drive (creates placeholders in online mode)
        if fresh or mapping_stale or not self.sync_manager.last_change_token:
            if mapping_stale:
                logging.info("Mapping is stale — clearing and re-running initial sync.")
            self.sync_manager.initial_sync_from_drive()
        else:
            logging.info(
                "Skipping initial sync. last_change_token found. Will check for remote changes."
            )

        # 5. Initialize and start local file system monitor
        event_handler = DriveSyncEventHandler(self.sync_manager)
        self.observer = Observer()
        self.observer.schedule(event_handler, LOCAL_SYNC_FOLDER, recursive=True)
        self.observer.start()
        logging.info(
            f"Started monitoring local directory: {os.path.abspath(LOCAL_SYNC_FOLDER)}"
        )

        # 6. Start remote sync thread
        self.remote_sync_thread = RemoteSyncThread(self.sync_manager, self.stop_event)
        self.remote_sync_thread.sync_status_signal.connect(self._on_tray_message)
        self.remote_sync_thread.start()
        logging.info(
            f"Started remote sync thread, checking every {REMOTE_SYNC_INTERVAL_SECONDS} seconds."
        )

        # 7. Start rollback thread
        self.rollback_thread = RollbackThread(self.sync_manager, self.stop_event)
        self.rollback_thread.rollback_status_signal.connect(self._on_tray_message)
        self.rollback_thread.start()
        logging.info(
            f"Started rollback thread, checking every {ROLLBACK_CHECK_INTERVAL_SECONDS} seconds."
        )

        self._sync_started = True
        self._on_tray_message("Sync started.")

    def stop_sync(self):
        """Stop all sync components."""
        if not self._sync_started:
            return

        logging.info("Stopping sync...")
        self.stop_event.set()

        if self.observer and self.observer.is_alive():
            self.observer.stop()
            self.observer.join()

        if self.remote_sync_thread and self.remote_sync_thread.isRunning():
            self.remote_sync_thread.quit()
            self.remote_sync_thread.wait()

        if self.rollback_thread and self.rollback_thread.isRunning():
            self.rollback_thread.quit()
            self.rollback_thread.wait()

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
        """Open the local sync folder in the file manager."""
        self.tray_icon._open_sync_folder()

    def manual_sync(self):
        """Trigger a manual sync from Drive.

        If the mapping is stale (references a different folder), performs a
        full re-scan to create placeholders in the current sync folder.
        Otherwise runs incremental change detection via sync_from_remote().
        """
        if not self._sync_started:
            self._on_tray_message("Select a sync folder in Settings first.")
            return
        if self.sync_manager:
            logging.info("Manual sync triggered.")
            self._on_tray_message("Manual sync started...")

            # Check for stale mapping (folder changed since last run)
            if self.sync_manager.is_mapping_for_other_folder():
                logging.warning(
                    "Manual sync: mapping is stale (different folder). "
                    "Running full re-scan to create placeholders."
                )
                self._on_tray_message("Mapping is stale. Running full re-scan...")
                threading.Thread(
                    target=self.sync_manager.initial_sync_from_drive, daemon=True
                ).start()
            else:
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
        self.settings_window.show()
        self.settings_window.activateWindow()

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
