import os
import logging
import sys
import subprocess

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QSystemTrayIcon,
    QMenu,
    QCheckBox,
)
from PyQt6.QtGui import QIcon, QAction

from config import LOCAL_SYNC_FOLDER, TOKEN_FILE, SCOPES, save_sync_folder
from auth import get_user_email, logout_google_account
from autostart import is_autostart_enabled, enable_autostart, disable_autostart


class SettingsWindow(QMainWindow):
    def __init__(self, sync_app):
        super().__init__()
        self.sync_app = sync_app
        self.setWindowTitle("gdrive-linux Settings")
        self.setGeometry(100, 100, 400, 300)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # Sync Folder Location
        layout.addWidget(QLabel("<b>Sync Folder Location:</b>"))
        self.sync_folder_label = QLabel(LOCAL_SYNC_FOLDER)
        layout.addWidget(self.sync_folder_label)
        self.sync_status_label = QLabel(self._sync_status_text())
        self.sync_status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.sync_status_label)
        change_folder_btn = QPushButton(
            "Select Sync Folder" if not self.sync_app._sync_started else "Change Folder"
        )
        change_folder_btn.clicked.connect(self._change_sync_folder)
        layout.addWidget(change_folder_btn)

        # Google Account Status
        layout.addWidget(QLabel("<b>Google Account:</b>"))
        self.google_account_label = QLabel("Not logged in")
        layout.addWidget(self.google_account_label)
        self.update_google_account_status()

        logout_btn = QPushButton("Log Out")
        logout_btn.clicked.connect(self._logout_google_account)
        layout.addWidget(logout_btn)

        # Manual Sync Button
        manual_sync_btn = QPushButton("Manual Sync Now")
        manual_sync_btn.clicked.connect(self.sync_app.manual_sync)
        layout.addWidget(manual_sync_btn)

        # Autostart toggle
        self.autostart_checkbox = QCheckBox("Start on login (autostart)")
        self.autostart_checkbox.setChecked(is_autostart_enabled())
        self.autostart_checkbox.toggled.connect(self._toggle_autostart)
        layout.addWidget(self.autostart_checkbox)

        layout.addStretch()  # Push content to top

    def _sync_status_text(self):
        if not self.sync_app._sync_started:
            return "⚠ Not syncing — select a folder above to start."
        return "✅ Sync active"

    def _change_sync_folder(self):
        new_folder = QFileDialog.getExistingDirectory(
            self, "Select Sync Folder", LOCAL_SYNC_FOLDER
        )
        if not new_folder or new_folder == LOCAL_SYNC_FOLDER:
            return

        # If sync is already running in a different folder, warn
        if self.sync_app._sync_started:
            reply = QMessageBox.question(
                self,
                "Change Sync Folder",
                "Changing the sync folder will stop current synchronization "
                "and start fresh with the new location.\n\n"
                f"New folder: {new_folder}\n\n"
                "Current mapping and local files will NOT be affected. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.sync_app.stop_sync()

        # Persist the new folder
        if not save_sync_folder(new_folder):
            QMessageBox.critical(
                self, "Error", "Failed to save the new sync folder path."
            )
            return

        self.sync_folder_label.setText(new_folder)
        self.sync_app.start_sync(fresh=True)
        self.sync_status_label.setText(self._sync_status_text())

        QMessageBox.information(
            self,
            "Sync Started",
            f"Synchronization started in:\n{new_folder}",
        )
        logging.info("Sync folder set to: %s. Sync started.", new_folder)

    def _toggle_autostart(self, checked: bool):
        """Enable or disable autostart."""
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

    def _logout_google_account(self):
        reply = QMessageBox.question(
            self,
            "Log Out",
            "Are you sure you want to log out from your Google account? This will stop synchronization.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if logout_google_account():  # Call the function from auth.py
                self.update_google_account_status()
                QMessageBox.information(
                    self,
                    "Logged Out",
                    "You have been logged out. Please restart the application.",
                )
                logging.info("Google account logged out.")
            else:
                QMessageBox.information(
                    self,
                    "Not Logged In",
                    "You are not currently logged in or token file not found.",
                )

    def update_google_account_status(self):
        if os.path.exists(TOKEN_FILE):
            try:
                # We need the creds object to get the email
                from google.oauth2.credentials import Credentials

                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

                user_email = get_user_email(creds)  # Call the function from auth.py
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
                logging.error(f"Error updating Google account status: {e}")
                self.google_account_label.setText(
                    "Token invalid. Please restart to re-authenticate."
                )
        else:
            self.google_account_label.setText("Not logged in")


class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, parent=None, sync_app=None):
        super().__init__(parent)
        self.sync_app = sync_app
        self.setIcon(QIcon.fromTheme("drive-harddisk"))
        self.setToolTip("gdrive-linux")
        self._user_email = None

        menu = QMenu()

        # User email label (disabled, informational only)
        self.email_action = QAction("Not logged in", self)
        self.email_action.setEnabled(False)
        menu.addAction(self.email_action)
        menu.addSeparator()

        open_folder_action = QAction("Open Sync Folder", self)
        open_folder_action.triggered.connect(self._open_sync_folder)
        menu.addAction(open_folder_action)

        manual_sync_action = QAction("Manual Sync", self)
        manual_sync_action.triggered.connect(self.sync_app.manual_sync)
        menu.addAction(manual_sync_action)

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
        """Fetch the authenticated user's email from stored token."""
        if os.path.exists(TOKEN_FILE):
            try:
                from google.oauth2.credentials import Credentials

                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                return get_user_email(creds)
            except Exception:
                return None
        return None

    def _update_user_email(self):
        """Update the tray tooltip and menu with the user email."""
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
            "gdrive-linux", message, QSystemTrayIcon.MessageIcon.Information, 5000
        )

    def _on_tray_message_clicked(self):
        logging.info("Tray message clicked.")
        # Optionally open settings or main window

    def _on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Left-click
            self.sync_app.show_settings()  # Show settings on left-click

    def _open_sync_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(LOCAL_SYNC_FOLDER)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", LOCAL_SYNC_FOLDER])
            else:  # Linux
                subprocess.Popen(["xdg-open", LOCAL_SYNC_FOLDER])
            logging.info(f"Opened sync folder: {LOCAL_SYNC_FOLDER}")
        except Exception as e:
            logging.error(f"Could not open sync folder: {e}")
            self._show_tray_message(f"Error opening folder: {e}")
