import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

from config import REMOTE_SYNC_INTERVAL_SECONDS


class RemoteSyncThread(QThread):
    sync_status_signal = pyqtSignal(str)

    def __init__(self, sync_manager, stop_event):
        super().__init__()
        self.sync_manager = sync_manager
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.sync_manager.sync_from_remote()
                self.sync_status_signal.emit("Last remote sync: " + time.strftime("%H:%M:%S"))
            except Exception as e:
                logging.error(f"Error during remote sync: {e}")
                self.sync_status_signal.emit(f"Remote sync error: {e}")
            self.stop_event.wait(REMOTE_SYNC_INTERVAL_SECONDS)
        logging.info("Remote sync thread stopped.")
