import os
import logging
import sys
import subprocess
import threading

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QSystemTrayIcon,
    QMenu,
    QCheckBox,
)
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import pyqtSlot

import config
from config import TOKEN_FILE, SCOPES, CACHE_MAX_SIZE_MB, MAPPING_FILE, SETTINGS_FILE
from auth import get_user_email, logout_google_account, reauthenticate_google_drive
from autostart import is_autostart_enabled, enable_autostart, disable_autostart


class SettingsWindow(QMainWindow):
    def __init__(self, sync_app):
        super().__init__()
        self.sync_app = sync_app
        self.setWindowTitle("gdrive-linux Settings")
        self.setGeometry(100, 100, 420, 360)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # Mount point info
        layout.addWidget(QLabel("<b>Network Drive Location:</b>"))
        mount_label = QLabel(config.FUSE_MOUNT_POINT)
        mount_label.setWordWrap(True)
        layout.addWidget(mount_label)

        status_text = "✅ Mounted" if sync_app._sync_started else "⚠ Not mounted"
        status_label = QLabel(status_text)
        status_label.setStyleSheet("color: gray;")
        layout.addWidget(status_label)

        # Google Account Status
        layout.addWidget(QLabel("<b>Google Account:</b>"))
        self.google_account_label = QLabel("Not logged in")
        layout.addWidget(self.google_account_label)

        # Auth button — toggles between Log Out and Log In
        self.auth_btn = QPushButton()
        layout.addWidget(self.auth_btn)

        # Now safe to update UI elements that reference auth_btn
        self.update_google_account_status()

        # Manual Sync Button
        manual_sync_btn = QPushButton("Manual Sync Now")
        manual_sync_btn.clicked.connect(self.sync_app.manual_sync)
        layout.addWidget(manual_sync_btn)

        # Autostart toggle
        self.autostart_checkbox = QCheckBox("Start on login (autostart)")
        self.autostart_checkbox.setChecked(is_autostart_enabled())
        self.autostart_checkbox.toggled.connect(self._toggle_autostart)
        layout.addWidget(self.autostart_checkbox)

        # --- Cache section ---
        layout.addSpacing(10)
        cache_header = QLabel("<b>Disk Cache:</b>")
        layout.addWidget(cache_header)

        self.cache_size_label = QLabel(self._format_cache_size())
        self.cache_size_label.setStyleSheet("color: gray;")
        layout.addWidget(self.cache_size_label)

        clear_cache_btn = QPushButton("Clear Cache")
        clear_cache_btn.clicked.connect(self._clear_cache)
        layout.addWidget(clear_cache_btn)

        # --- Reset section ---
        layout.addSpacing(10)
        reset_header = QLabel("<b>Reset Application:</b>")
        layout.addWidget(reset_header)
        reset_desc = QLabel(
            "Clears cache, mapping, settings, and logs you out. "
            "Use this if the app gets into a broken state."
        )
        reset_desc.setWordWrap(True)
        reset_desc.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(reset_desc)

        reset_btn = QPushButton("Reset All Data")
        reset_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #c0392b; font-weight: bold; "
            "padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #e74c3c; }"
        )
        reset_btn.clicked.connect(self._reset_application)
        layout.addWidget(reset_btn)

        layout.addStretch()

    def _format_cache_size(self) -> str:
        try:
            from disk_cache import get_cache_size

            size_bytes = get_cache_size()
            if size_bytes < 1024:
                return f"Cache size: {size_bytes} B (limit: {CACHE_MAX_SIZE_MB} MB)"
            elif size_bytes < 1024 * 1024:
                return f"Cache size: {size_bytes / 1024:.1f} KB (limit: {CACHE_MAX_SIZE_MB} MB)"
            else:
                return f"Cache size: {size_bytes / (1024 * 1024):.1f} MB (limit: {CACHE_MAX_SIZE_MB} MB)"
        except Exception as e:
            logging.error("Error reading cache size: %s", e)
            return "Cache size: unknown"

    def _clear_cache(self):
        reply = QMessageBox.question(
            self,
            "Clear Cache",
            "Are you sure you want to clear the disk cache?\n"
            "Cached file chunks will need to be re-downloaded from Google Drive.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                from disk_cache import clear_cache

                freed = clear_cache()
                freed_mb = freed / (1024 * 1024)
                self.cache_size_label.setText(self._format_cache_size())
                QMessageBox.information(
                    self,
                    "Cache Cleared",
                    f"Cleared {freed_mb:.1f} MB of cached data.\n"
                    "Files will be re-fetched from Google Drive as needed.",
                )
                logging.info("Cache cleared manually (%d bytes freed).", freed)
            except Exception as e:
                logging.error("Failed to clear cache: %s", e)
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to clear cache: {e}",
                )

    def _reset_application(self):
        """Reset all application data and log out.

        Clears:
        - Disk cache (downloaded file chunks)
        - Sync mapping (drive file index)
        - Settings (window prefs, autostart flag)
        - OAuth token (logged-in Google account)

        The user will need to re-authenticate on next launch.
        """
        reply = QMessageBox.warning(
            self,
            "Reset All Data",
            "This will delete ALL local application data:\n\n"
            "• Disk cache (downloaded file chunks)\n"
            "• Sync mapping (file index)\n"
            "• Settings\n"
            "• Google account login (token)\n\n"
            "After reset, the app will log you out and you'll need to "
            "sign in again on next launch.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Double-check with the user
        confirm = QMessageBox.warning(
            self,
            "Are you sure?",
            "This cannot be undone. All cached data and settings will be lost.\n\n"
            "Proceed with reset?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        logging.info("Starting application reset...")

        # 1. Stop sync (unmounts FUSE, stops threads)
        self.sync_app.stop_sync()

        # 2. Clear disk cache
        try:
            from disk_cache import clear_cache

            clear_cache()
            logging.info("Cache cleared during reset.")
        except Exception as e:
            logging.warning("Failed to clear cache during reset: %s", e)

        # 3. Delete mapping file
        for file_path in [MAPPING_FILE, SETTINGS_FILE, TOKEN_FILE]:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logging.info("Deleted: %s", file_path)
            except Exception as e:
                logging.warning("Failed to delete %s: %s", file_path, e)

        # 4. Update UI
        self.cache_size_label.setText(self._format_cache_size())
        self.update_google_account_status()

        QMessageBox.information(
            self,
            "Reset Complete",
            "All application data has been cleared.\n\n"
            "The app will log you out and you can sign in again "
            "on the next launch.",
        )
        logging.info("Application reset complete.")

    def _logout_google_account(self):
        reply = QMessageBox.question(
            self,
            "Log Out",
            "Are you sure you want to log out from your Google account? "
            "This will unmount the drive and stop synchronization.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if logout_google_account():
                self.sync_app.stop_sync()
                self.update_google_account_status()
                QMessageBox.information(
                    self,
                    "Logged Out",
                    "You have been logged out. Click 'Log In' to sign in with a different account.",
                )
                logging.info("Google account logged out.")
            else:
                QMessageBox.information(
                    self,
                    "Not Logged In",
                    "You are not currently logged in or token file not found.",
                )

    def _login_google_account(self):
        """Trigger re-auth flow in a background thread to keep UI responsive."""
        self.auth_btn.setEnabled(False)
        self.auth_btn.setText("Authenticating...")

        def _do_login():
            try:
                creds = reauthenticate_google_drive()
                if creds:
                    from googleapiclient.discovery import build

                    # Rebuild the drive service and sync manager
                    drive_service = build("drive", "v3", credentials=creds)
                    self.sync_app.drive_service = drive_service
                    self.sync_app.sync_manager = None
                    self.sync_app.start_sync()
                    logging.info("Re-authentication successful. Drive remounted.")
                else:
                    logging.error("Re-authentication failed.")
            except Exception as e:
                logging.error("Re-authentication error: %s", e)
            finally:
                # Schedule UI update on the main thread
                from PyQt6.QtCore import QTimer

                QTimer.singleShot(0, self.update_google_account_status)

        threading.Thread(target=_do_login, daemon=True).start()

    def _toggle_autostart(self, checked: bool):
        if checked:
            if enable_autostart():
                QMessageBox.information(
                    self,
                    "Autostart Enabled",
                    "gdrive-linux will start automatically on login.",
                )
            else:
                self.autostart_checkbox.setChecked(False)
        else:
            if disable_autostart():
                QMessageBox.information(
                    self,
                    "Autostart Disabled",
                    "gdrive-linux will no longer start on login.",
                )
            else:
                self.autostart_checkbox.setChecked(True)

    def update_google_account_status(self):
        if os.path.exists(TOKEN_FILE):
            try:
                from google.oauth2.credentials import Credentials

                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

                user_email = get_user_email(creds)
                if user_email:
                    self.google_account_label.setText(f"Logged in as: {user_email}")
                elif creds.valid or creds.refresh_token:
                    self.google_account_label.setText(
                        "Logged in (token exists, email not fetched)"
                    )
                else:
                    self.google_account_label.setText(
                        "Token expired/invalid. Please restart to re-authenticate."
                    )
            except Exception as e:
                logging.error("Error updating Google account status: %s", e)
                self.google_account_label.setText(
                    "Token invalid. Please restart to re-authenticate."
                )
        else:
            self.google_account_label.setText("Not logged in")

        # Update auth button based on login state
        self._update_auth_button()

        # Also update tray icon email display
        if self.sync_app.tray_icon:
            self.sync_app.tray_icon._update_user_email()

    def _update_auth_button(self):
        """Set the auth button text and action based on login state."""
        if not hasattr(self, "auth_btn") or self.auth_btn is None:
            return  # Widget not created yet
        is_logged_in = os.path.exists(TOKEN_FILE)
        # Safely disconnect all existing connections before reconnecting
        try:
            self.auth_btn.clicked.disconnect()
        except TypeError:
            pass  # No existing connections — this is fine
        if is_logged_in:
            self.auth_btn.setText("Log Out")
            self.auth_btn.clicked.connect(self._logout_google_account)
        else:
            self.auth_btn.setText("Log In")
            self.auth_btn.clicked.connect(self._login_google_account)
        self.auth_btn.setEnabled(True)


class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, parent=None, sync_app=None):
        super().__init__(parent)
        self.sync_app = sync_app
        self.setIcon(QIcon.fromTheme("drive-harddisk"))
        self.setToolTip("gdrive-linux")
        self._user_email = None

        menu = QMenu()

        self.email_action = QAction("Not logged in", self)
        self.email_action.setEnabled(False)
        menu.addAction(self.email_action)
        menu.addSeparator()

        # Cache info and clear action
        self.cache_action = QAction("", self)
        self.cache_action.setEnabled(False)
        menu.addAction(self.cache_action)
        self._update_cache_action()

        clear_cache_action = QAction("Clear Cache", self)
        clear_cache_action.triggered.connect(self._clear_cache_from_tray)
        menu.addAction(clear_cache_action)
        menu.addSeparator()

        # Refresh cache display when the menu opens
        menu.aboutToShow.connect(self._update_cache_action)

        # Refresh cache display every 5 seconds while menu is open
        self._cache_refresh_timer = None

        open_folder_action = QAction(
            f"Open {os.path.basename(config.FUSE_MOUNT_POINT)}", self
        )
        open_folder_action.triggered.connect(self._open_sync_folder)
        menu.addAction(open_folder_action)

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.sync_app.show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        reset_action = QAction("Reset All Data", self)
        reset_action.triggered.connect(self._reset_from_tray)
        menu.addAction(reset_action)

        menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.sync_app.quit_app)
        menu.addAction(exit_action)

        self.setContextMenu(menu)
        self._update_user_email()
        self.show()
        self.messageClicked.connect(self._on_tray_message_clicked)
        self.activated.connect(self._on_tray_icon_activated)

    def _get_user_email(self):
        if os.path.exists(TOKEN_FILE):
            try:
                from google.oauth2.credentials import Credentials

                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                return get_user_email(creds)
            except Exception:
                return None
        return None

    def _update_user_email(self):
        email = self._get_user_email()
        if email:
            self._user_email = email
            self.setToolTip(f"gdrive-linux — {email}")
            self.email_action.setText(f"Signed in as {email}")
        else:
            self._user_email = None
            self.setToolTip("gdrive-linux — Not logged in")
            self.email_action.setText("Not logged in")

    def _update_cache_action(self):
        try:
            from disk_cache import get_cache_size

            size_bytes = get_cache_size()
            if size_bytes < 1024:
                text = f"Cache: {size_bytes} B"
            elif size_bytes < 1024 * 1024:
                text = f"Cache: {size_bytes / 1024:.1f} KB"
            else:
                text = f"Cache: {size_bytes / (1024 * 1024):.1f} MB"
        except Exception:
            text = "Cache: unknown"
        self.cache_action.setText(text)

    def _clear_cache_from_tray(self):
        reply = QMessageBox.question(
            None,
            "Clear Cache",
            "Are you sure you want to clear the disk cache?\n"
            "Cached file chunks will need to be re-downloaded from Google Drive.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                from disk_cache import clear_cache

                freed = clear_cache()
                self._update_cache_action()
                freed_mb = freed / (1024 * 1024)
                self._show_tray_message(f"Cache cleared: {freed_mb:.1f} MB freed.")
                logging.info("Cache cleared from tray (%d bytes freed).", freed)
            except Exception as e:
                logging.error("Failed to clear cache from tray: %s", e)
                self._show_tray_message(f"Failed to clear cache: {e}")

    def _reset_from_tray(self):
        """Open settings and trigger the reset from there (reuses same logic)."""
        self.sync_app.show_settings()
        if self.sync_app.settings_window:
            self.sync_app.settings_window._reset_application()

    @pyqtSlot(str)
    def _show_tray_message(self, message):
        self.showMessage(
            "gdrive-linux",
            message,
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def _on_tray_message_clicked(self):
        logging.info("Tray message clicked.")

    def _on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._open_sync_folder()

    def _open_sync_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(config.FUSE_MOUNT_POINT)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", config.FUSE_MOUNT_POINT])
            else:
                subprocess.Popen(["xdg-open", config.FUSE_MOUNT_POINT])
            logging.info("Opened mount point: %s", config.FUSE_MOUNT_POINT)
        except Exception as e:
            logging.error("Could not open mount point: %s", e)
            self._show_tray_message(f"Error opening folder: {e}")
