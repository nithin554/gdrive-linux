import time
import logging
import os
from PyQt6.QtCore import QThread, pyqtSignal

from config import (
    REMOTE_SYNC_INTERVAL_SECONDS,
    ROLLBACK_PERIOD_SECONDS,
    ROLLBACK_CHECK_INTERVAL_SECONDS,
)


class RemoteSyncThread(QThread):
    # Signal to update GUI or show notifications
    sync_status_signal = pyqtSignal(str)

    def __init__(self, sync_manager, stop_event):
        super().__init__()
        self.sync_manager = sync_manager
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.sync_manager.sync_from_remote()
                self.sync_status_signal.emit(
                    "Last remote sync: " + time.strftime("%H:%M:%S")
                )
            except Exception as e:
                logging.error(f"Error during remote sync: {e}")
                self.sync_status_signal.emit(f"Remote sync error: {e}")
            self.stop_event.wait(REMOTE_SYNC_INTERVAL_SECONDS)
        logging.info("Remote sync thread stopped.")


class RollbackThread(QThread):
    rollback_status_signal = pyqtSignal(str)

    def __init__(self, sync_manager, stop_event):
        super().__init__()
        self.sync_manager = sync_manager
        self.stop_event = stop_event

    def run(self):
        # Wait before first check to allow initial sync to complete
        self.stop_event.wait(ROLLBACK_CHECK_INTERVAL_SECONDS)

        while not self.stop_event.is_set():
            logging.info("Checking for files to rollback to 'online' mode.")
            current_time = time.time()

            # Iterate over a copy to avoid RuntimeError: dictionary changed size during iteration
            for local_path, file_info in list(
                self.sync_manager.local_file_info.items()
            ):
                if self.stop_event.is_set():  # Check stop event within loop
                    break

                if (
                    file_info.get("mode") == "local"
                    and file_info.get("mimeType")
                    != "application/vnd.google-apps.folder"
                ):
                    last_accessed = file_info.get("last_accessed_time", 0)

                    # Only consider rolling back if it was previously an online file (last_accessed_time was set)
                    # and it's been longer than the rollback period
                    if (
                        last_accessed > 0
                        and (current_time - last_accessed) > ROLLBACK_PERIOD_SECONDS
                    ):
                        # Add a small grace period to avoid immediate rollback after access
                        # This heuristic assumes if it was accessed very recently, it might still be in use.
                        # A more robust check would involve OS-specific file lock detection (complex without FUSE).
                        if (current_time - last_accessed) < (
                            ROLLBACK_PERIOD_SECONDS + 60
                        ):  # 60 seconds grace
                            logging.debug(
                                f"Skipping rollback for {local_path}: recently accessed within grace period."
                            )
                            continue

                        logging.info(
                            f"Rolling back '{local_path}' to 'online' mode (placeholder)."
                        )
                        if self.sync_manager.set_file_mode(local_path, "online"):
                            self.rollback_status_signal.emit(
                                f"Rolled back: {os.path.basename(local_path)}"
                            )
                        else:
                            self.rollback_status_signal.emit(
                                f"Rollback failed: {os.path.basename(local_path)}"
                            )

            self.stop_event.wait(ROLLBACK_CHECK_INTERVAL_SECONDS)
        logging.info("Rollback thread stopped.")
