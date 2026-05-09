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

from config import FUSE_MOUNT_POINT, TOKEN_FILE, SCOPES
from auth import get_user_email, logout_google_account, reauthenticate_google_drive
from autostart import is_autostart_enabled, enable_autostart, disable_autostart


class SettingsWindow(QMainWindow):
    def __init__(self, sync_app):
        super().__init__()
        self.sync_app = sync_app
        self.setWindowTitle("gdrive-linux Settings")
        self.setGeometry(100, 100, 400, 250)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # Mount point info
        layout.addWidget(QLabel("<b>Network Drive Location:</b>"))
        mount_label = QLabel(FUSE_MOUNT_POINT)
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

        layout.addStretch()

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

        open_folder_action = QAction(f"Open {os.path.basename(FUSE_MOUNT_POINT)}", self)
        open_folder_action.triggered.connect(self._open_sync_folder)
        menu.addAction(open_folder_action)

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.sync_app.show_settings)
        menu.addAction(settings_action)

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
                os.startfile(FUSE_MOUNT_POINT)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", FUSE_MOUNT_POINT])
            else:
                subprocess.Popen(["xdg-open", FUSE_MOUNT_POINT])
            logging.info("Opened mount point: %s", FUSE_MOUNT_POINT)
        except Exception as e:
            logging.error("Could not open mount point: %s", e)
            self._show_tray_message(f"Error opening folder: {e}")
