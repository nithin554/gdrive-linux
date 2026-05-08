import os
import logging
import threading

from watchdog.events import FileSystemEventHandler

from sync_manager import SyncManager
from config import LOCAL_SYNC_FOLDER

# Debounce window in seconds — coalesces rapid file change events
# (e.g., text editor saves, repeated writes) into a single action
_DEBOUNCE_SECONDS = 1.0


class DriveSyncEventHandler(FileSystemEventHandler):
    """
    Handles local file system events and triggers corresponding Google Drive actions.

    Uses debouncing to coalesce rapid successive events (e.g., editor saves)
    into a single sync operation, reducing API calls and preventing conflicts.
    """

    def __init__(self, sync_manager: SyncManager):
        super().__init__()
        self.sync_manager = sync_manager
        self._debounce_timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        logging.info(
            "DriveSyncEventHandler initialized (debounce=%.1fs).", _DEBOUNCE_SECONDS
        )

    @staticmethod
    def _get_relative_path(absolute_path):
        """Converts an absolute path to a path relative to the LOCAL_SYNC_FOLDER."""
        return os.path.relpath(absolute_path, LOCAL_SYNC_FOLDER)

    def _debounce(self, path: str, callback):
        """Cancel any pending timer for *path* and schedule *callback* after debounce delay."""
        with self._lock:
            # Cancel existing timer if any
            existing = self._debounce_timers.pop(path, None)
            if existing is not None:
                existing.cancel()
            # Schedule new timer
            timer = threading.Timer(
                _DEBOUNCE_SECONDS, self._execute_callback, args=[path, callback]
            )
            timer.daemon = True
            self._debounce_timers[path] = timer
            timer.start()

    def _execute_callback(self, path: str, callback):
        """Thread-safe callback execution with cleanup."""
        with self._lock:
            self._debounce_timers.pop(path, None)
        try:
            callback()
        except Exception as e:
            logging.error("Error handling event for %s: %s", path, e)

    def _ignore_event(self, src_path: str) -> bool:
        """Check if the event should be ignored (sync in progress or temp/swap file)."""
        # Ignore swap/temp files
        basename = os.path.basename(src_path)
        if (
            basename.startswith(".")
            or basename.endswith("~")
            or basename.endswith(".swp")
            or basename.endswith(".swx")
        ):
            return True
        if self.sync_manager.is_path_in_sync_progress(src_path):
            return True
        return False

    # ---- Event handlers ----

    def on_created(self, event):
        if self._ignore_event(event.src_path):
            return
        local_path = event.src_path
        relative_path = self._get_relative_path(local_path)

        if not event.is_directory:
            logging.info(
                "Local file created: %s. Scheduling Drive upload...", relative_path
            )
            self._debounce(
                local_path, lambda: self.sync_manager.upload_file(local_path)
            )
        else:
            logging.info(
                "Local directory created: %s. Triggering Drive folder creation...",
                relative_path,
            )
            self.sync_manager.create_folder(local_path)

    def on_deleted(self, event):
        if self._ignore_event(event.src_path):
            return
        local_path = event.src_path
        relative_path = self._get_relative_path(local_path)

        # Flush any pending debounced events for this path
        with self._lock:
            timer = self._debounce_timers.pop(local_path, None)
            if timer:
                timer.cancel()

        # Remove from mapping only — do NOT propagate deletion to Google Drive
        if not event.is_directory:
            logging.info(
                "Local file deleted: %s. Removing from mapping (no Drive deletion).",
                relative_path,
            )
            self.sync_manager._remove_from_mapping(local_path)
        else:
            logging.info(
                "Local directory deleted: %s. Removing from mapping (no Drive deletion).",
                relative_path,
            )
            self.sync_manager._remove_folder_from_mapping(local_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        if self._ignore_event(event.src_path):
            return

        local_file_path = event.src_path
        relative_path = self._get_relative_path(local_file_path)

        # Handle placeholder files immediately (no debounce — access is user-driven)
        file_info = self.sync_manager.local_file_info.get(local_file_path)
        if (
            file_info
            and isinstance(file_info, dict)
            and file_info.get("mode") == "online"
        ):
            logging.info(
                "Local modification of placeholder file %s. Downloading content first.",
                relative_path,
            )
            if self.sync_manager.set_file_mode(local_file_path, "local"):
                logging.info(
                    "Mode for %s changed to 'local'. Now scheduling Drive update.",
                    relative_path,
                )
                self._debounce(
                    local_file_path,
                    lambda: self.sync_manager.update_file(local_file_path),
                )
            else:
                logging.error(
                    "Failed to change mode to 'local' for %s. Skipping Drive update.",
                    relative_path,
                )
            return

        logging.info(
            "Local file modified: %s. Scheduling debounced Drive update...",
            relative_path,
        )
        self._debounce(
            local_file_path, lambda: self.sync_manager.update_file(local_file_path)
        )

    def on_moved(self, event):
        if self._ignore_event(event.src_path) or self._ignore_event(event.dest_path):
            return

        relative_src_path = self._get_relative_path(event.src_path)
        relative_dest_path = self._get_relative_path(event.dest_path)

        # Cancel any pending debounced events for the source path
        with self._lock:
            timer = self._debounce_timers.pop(event.src_path, None)
            if timer:
                timer.cancel()

        logging.info(
            "Local item moved/renamed from %s to %s. Triggering Drive update...",
            relative_src_path,
            relative_dest_path,
        )
        self.sync_manager.move_item(event.src_path, event.dest_path)

    def on_opened(self, event):
        """Handles file open events for on-demand download (no debounce needed).

        When a placeholder file (mode='online') is opened for reading, this
        triggers the actual content download from Google Drive.
        """
        if event.is_directory:
            return
        if self._ignore_event(event.src_path):
            return

        local_file_path = event.src_path
        relative_path = self._get_relative_path(local_file_path)

        file_info = self.sync_manager.local_file_info.get(local_file_path)
        if file_info and isinstance(file_info, dict):
            if file_info.get("mode") == "online":
                logging.info(
                    "Placeholder file '%s' opened. Downloading content...",
                    relative_path,
                )
                self.sync_manager.set_file_mode(local_file_path, "local")
            elif file_info.get("mode") == "local":
                self.sync_manager.update_last_accessed_time(local_file_path)
