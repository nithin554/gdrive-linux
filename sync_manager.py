import io
import json
import logging
import os
import threading
import time

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

import config
from config import (
    DEFAULT_FILE_MODE,
    MAPPING_FILE,
)

# config.LOCAL_SYNC_FOLDER is set dynamically after authentication.
# All runtime code uses config.LOCAL_SYNC_FOLDER.


class SyncManager:
    def __init__(self, drive_service, service_pool=None):
        self.drive_service = drive_service
        # Thread-local service pool — allows parallel reads from FUSE threads.
        # Each thread gets its own drive_service instance with an independent
        # HTTP connection pool, eliminating lock contention for reads.
        # Falls back to the single drive_service if no pool provided.
        self._service_pool = service_pool
        # Maps local_path -> {id, mode, mimeType, size, last_accessed_time}
        self.local_file_info = {}
        self.drive_id_to_local_path = {}  # Maps drive_id -> local_path
        self.last_change_token = None  # Stores Drive Changes API page token
        # Paths currently being manipulated by SyncManager
        self._sync_in_progress_files = set()
        # Lock for thread-safe access to mapping data from FUSE and sync threads
        self._mapping_lock = threading.Lock()
        # Lock for serialising Drive API **write** calls that also mutate
        # shared mapping state (create, update, delete, move).
        # Read calls (get_media, export, changes.list, files.list) use the
        # thread-local service from the pool and do NOT need this lock.
        #
        # Must be RLock (reentrant) because ``_get_drive_folder_id()`` acquires
        # this lock internally, and several callers (create_folder, upload_file,
        # move_item, fuse_drive.create) also acquire it — the lock must support
        # recursive acquisition by the same thread to avoid deadlock.
        self._drive_api_lock = threading.RLock()
        self._load_mapping()
        logging.info("SyncManager initialized and mapping loaded.")

    # ------------------------------------------------------------------
    # Thread-safe service access
    # ------------------------------------------------------------------

    def _get_service(self):
        """Return a Drive service suitable for the **current thread**.

        For **read** operations (get_media, export, changes.list, files.list),
        this returns a per-thread service from the pool, allowing concurrent
        reads without lock contention.

        For **write** operations, use ``self.drive_service`` (the original
        shared service) inside ``self._drive_api_lock``.
        """
        if self._service_pool is not None:
            svc = self._service_pool.get()
            if svc is not None:
                return svc
        return self.drive_service

    def _load_mapping(self):
        """Loads the mapping from a JSON file.

        Also migrates any relative paths to absolute paths under
        config.LOCAL_SYNC_FOLDER, so the mapping is always consistent with the
        current FUSE mount point.
        """
        if os.path.exists(MAPPING_FILE):
            with open(MAPPING_FILE) as f:
                try:
                    data = json.load(f)
                    self.local_file_info = data.get("local_file_info", {})
                    self.drive_id_to_local_path = data.get("drive_id_to_local_path", {})
                    self.last_change_token = data.get("last_change_token", None)

                    # --- Path migration -------------------------------------------------
                    # Handles two scenarios:
                    #
                    # 1. Relative paths (e.g., "Videos/movie.mp4") — from older versions
                    #    where mapping paths were relative to a folder name. Rewrite them
                    #    to absolute paths under the current config.LOCAL_SYNC_FOLDER.
                    #
                    # 2. Absolute paths pointing to an OLD mount point (e.g., paths under
                    #    ~/Gdrive which no longer exists). Detect the old mount directory
                    #    and rewrite to the new one.
                    #
                    # Paths that are already under the current mount point are left untouched.
                    if self.local_file_info:
                        sample_key = next(iter(self.local_file_info))
                        mount = config.LOCAL_SYNC_FOLDER
                        if mount is None:
                            mount = os.path.expanduser("~")

                        needs_migration = not sample_key.startswith(mount + os.sep) and sample_key != mount

                        if needs_migration:
                            logging.info(
                                "Migrating mapping paths to current mount point (sample: '%s', target: '%s').",
                                sample_key,
                                mount,
                            )
                            new_file_info = {}
                            new_drive_map = {}

                            if sample_key.startswith("/"):
                                # Absolute paths — likely from a previous mount point.
                                # Find the common old prefix to strip.
                                # Sample: /home/nithin/Gdrive/some/file
                                # Old mount: /home/nithin/Gdrive  (first 2 components after /)
                                # New mount: /home/nithin/Google Drive (email)
                                # Strategy: find the first path component that differs,
                                # and replace that component and everything after.
                                old_mount_prefix = os.path.commonpath(list(self.local_file_info.keys())[:100])
                                new_file_info = {}
                                new_drive_map = {}
                                for path, info in self.local_file_info.items():
                                    # Compute relative path within the old mount
                                    if path.startswith(old_mount_prefix):
                                        rel = path[len(old_mount_prefix) :].lstrip(os.sep)
                                    elif path.startswith("/"):
                                        # Fallback: strip /home/user/ prefix
                                        rel = os.path.join(*path.split(os.sep)[3:])
                                    else:
                                        rel = path
                                    new_path = os.path.join(mount, rel)
                                    new_file_info[new_path] = info
                                for (
                                    drive_id,
                                    path,
                                ) in self.drive_id_to_local_path.items():
                                    if path.startswith(old_mount_prefix):
                                        rel = path[len(old_mount_prefix) :].lstrip(os.sep)
                                    elif path.startswith("/"):
                                        rel = os.path.join(*path.split(os.sep)[3:])
                                    else:
                                        rel = path
                                    new_path = os.path.join(mount, rel)
                                    new_drive_map[drive_id] = new_path
                            else:
                                # Relative paths — prepend the mount point.
                                old_prefix = sample_key.split(os.sep)[0]
                                for path, info in self.local_file_info.items():
                                    if path.startswith(old_prefix):
                                        new_path = os.path.join(
                                            mount,
                                            path[len(old_prefix) + 1 :],
                                        )
                                    else:
                                        new_path = path
                                    new_file_info[new_path] = info
                                for (
                                    drive_id,
                                    path,
                                ) in self.drive_id_to_local_path.items():
                                    if path.startswith(old_prefix):
                                        new_path = os.path.join(
                                            mount,
                                            path[len(old_prefix) + 1 :],
                                        )
                                    else:
                                        new_path = path
                                    new_drive_map[drive_id] = new_path

                            self.local_file_info = new_file_info
                            self.drive_id_to_local_path = new_drive_map
                            self._save_mapping()
                    # --------------------------------------------------------------------

                    logging.info(f"Loaded {len(self.local_file_info)} items from {MAPPING_FILE}")
                    if self.last_change_token:
                        logging.info(f"Loaded last change token: {self.last_change_token}")
                except json.JSONDecodeError:
                    logging.warning(f"Could not decode {MAPPING_FILE}. Starting with empty mapping.")
        else:
            logging.info(f"No {MAPPING_FILE} found. Starting with empty mapping.")

    def is_mapping_for_other_folder(self) -> bool:
        """Check if the loaded mapping references a different sync folder.

        Returns True when the mapping contains paths that don't start with
        the current config.LOCAL_SYNC_FOLDER, indicating the user changed sync
        folders without clearing the old mapping.
        """
        if not self.local_file_info:
            return False  # Empty mapping — nothing to mismatch
        mapped_paths = list(self.local_file_info.keys())
        # Check if ALL paths are under the current sync folder
        all_match_current = all(p.startswith(config.LOCAL_SYNC_FOLDER) for p in mapped_paths)
        return not all_match_current

    def _save_mapping(self):
        """Saves the current mapping to a JSON file."""
        with open(MAPPING_FILE, "w") as f:
            json.dump(
                {
                    "local_file_info": self.local_file_info,
                    "drive_id_to_local_path": self.drive_id_to_local_path,
                    "last_change_token": self.last_change_token,
                },
                f,
                indent=4,
            )
        logging.debug("Saved mapping (%d items).", len(self.local_file_info))

    def is_path_in_sync_progress(self, path):
        """Checks if a given path is currently being manipulated by the SyncManager."""
        return path in self._sync_in_progress_files

    def _get_drive_folder_id(self, local_path_or_file, is_folder=False):
        """
        Ensures the Google Drive parent folder structure exists for a given local path
        and returns the Drive ID of the immediate parent folder.
        If local_path_or_file is a file, it returns the ID of its parent folder.
        If local_path_or_file is a folder, it returns its own ID (if it exists) or its parent's ID.
        """

        # Determine the local path of the folder whose Drive ID we need
        target_local_folder_path = local_path_or_file if is_folder else os.path.dirname(local_path_or_file)

        if not target_local_folder_path or target_local_folder_path == config.LOCAL_SYNC_FOLDER:
            # If it's the root sync folder itself, or a file directly in it, parent is 'root'
            return "root"

        # Build up the path components relative to config.LOCAL_SYNC_FOLDER
        relative_path_components = []
        temp_path = target_local_folder_path
        while temp_path and temp_path != config.LOCAL_SYNC_FOLDER:
            relative_path_components.insert(0, os.path.basename(temp_path))
            temp_path = os.path.dirname(temp_path)

        current_drive_parent_id = "root"
        current_local_folder_path = config.LOCAL_SYNC_FOLDER

        for component in relative_path_components:
            current_local_folder_path = os.path.join(current_local_folder_path, component)
            # Check if folder info exists in mapping
            folder_info = self.local_file_info.get(current_local_folder_path)

            drive_folder_id_from_mapping = None
            if (
                folder_info
                and isinstance(folder_info, dict)
                and folder_info.get("mimeType") == "application/vnd.google-apps.folder"
            ):
                drive_folder_id_from_mapping = folder_info.get("id")

            if drive_folder_id_from_mapping:
                current_drive_parent_id = drive_folder_id_from_mapping
            else:
                # Folder doesn't exist in mapping or is not a folder, create it on Drive
                logging.info(f"Creating missing Drive folder for local path: {current_local_folder_path}")
                folder_metadata = {
                    "name": component,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [current_drive_parent_id],
                }
                try:
                    # Add to ignore list before creating folder on Drive
                    self._sync_in_progress_files.add(current_local_folder_path)

                    with self._drive_api_lock:
                        folder = (
                            self.drive_service.files()
                            .create(body=folder_metadata, fields="id, name, parents")
                            .execute()
                        )

                    drive_folder_id = folder.get("id")
                    self.local_file_info[current_local_folder_path] = {
                        "id": drive_folder_id,
                        "mode": "local",  # Folders are always "local" in terms of content
                        "mimeType": "application/vnd.google-apps.folder",
                        "size": "0",
                    }
                    self.drive_id_to_local_path[drive_folder_id] = current_local_folder_path

                    self._save_mapping()
                    current_drive_parent_id = drive_folder_id
                except HttpError as error:
                    logging.error(f"Error creating Drive folder {current_local_folder_path}: {error}")
                    return None  # Critical error, cannot proceed without parent folder
                finally:
                    self._sync_in_progress_files.discard(current_local_folder_path)

        return current_drive_parent_id

    def upload_file(self, local_file_path):
        """Uploads a new file to Google Drive."""
        if local_file_path in self.local_file_info:
            logging.warning(f"File {local_file_path} already exists in mapping. Skipping upload.")
            return None  # Return None for consistency

        try:
            file_name = os.path.basename(local_file_path)
            # Use the enhanced _get_drive_folder_id to get the parent ID
            parent_id = self._get_drive_folder_id(local_file_path, is_folder=False)
            if parent_id is None:  # Handle error from _get_drive_folder_id
                logging.error(f"Could not determine parent ID for {local_file_path}. Skipping upload.")
                return None  # Return None for consistency

            file_metadata = {"name": file_name, "parents": [parent_id]}
            media = MediaFileUpload(local_file_path, mimetype="application/octet-stream")

            with self._drive_api_lock:
                file = (
                    self.drive_service.files()
                    .create(
                        body=file_metadata,
                        media_body=media,
                        fields="id, name, parents, mimeType, size",
                    )
                    .execute()
                )

            drive_file_id = file.get("id")
            self.local_file_info[local_file_path] = {
                "id": drive_file_id,
                "mode": "local",  # Locally created files are always "local"
                "mimeType": file.get("mimeType"),
                "size": file.get("size"),
                "last_accessed_time": time.time(),  # Track access time for rollback
            }
            self.drive_id_to_local_path[drive_file_id] = local_file_path
            self._save_mapping()  # Save mapping after each change
            logging.info(f"Uploaded '{file_name}' (ID: {drive_file_id}) to Drive. Local: {local_file_path}")
            return drive_file_id

        except HttpError as error:
            logging.error(f"Error uploading file {local_file_path}: {error}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error during file upload {local_file_path}: {e}")
            return None

    def update_file(self, local_file_path):
        """Updates an existing file on Google Drive."""
        file_info = self.local_file_info.get(local_file_path)

        if not file_info:
            logging.warning(f"File {local_file_path} not found in mapping for update. Attempting upload instead.")
            return self.upload_file(local_file_path)  # If not found, treat as new

        drive_file_id = file_info.get("id")  # Use .get() for safety
        if not drive_file_id:
            logging.error(f"Drive ID not found for local file {local_file_path}. Cannot update.")
            return None

        try:
            # Add to ignore list before reading local file for upload
            self._sync_in_progress_files.add(local_file_path)
            media = MediaFileUpload(local_file_path, mimetype="application/octet-stream")
            with self._drive_api_lock:
                updated_file = (
                    self.drive_service.files()
                    .update(
                        fileId=drive_file_id,
                        media_body=media,
                        fields="id, name, mimeType, size",
                    )
                    .execute()
                )

            # Update metadata in mapping
            if isinstance(file_info, dict):  # Ensure file_info is a dict before updating
                file_info["mimeType"] = updated_file.get("mimeType")
                file_info["size"] = updated_file.get("size")
                file_info["last_accessed_time"] = time.time()  # Update access time on modification

            self._save_mapping()

            logging.info(f"Updated '{os.path.basename(local_file_path)}' (ID: {drive_file_id}) on Drive.")
            return drive_file_id
        except HttpError as error:
            logging.error(f"Error updating file {local_file_path} (ID: {drive_file_id}): {error}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error during file update {local_file_path}: {e}")
            return None
        finally:
            self._sync_in_progress_files.discard(local_file_path)

    def delete_file(self, local_file_path):
        """Deletes a file from Google Drive."""
        file_info = self.local_file_info.get(local_file_path)

        if not file_info:
            logging.warning(f"File {local_file_path} not found in mapping for deletion. Skipping Drive deletion.")
            return False  # Return False for consistency

        drive_file_id = file_info.get("id")  # Use .get() for safety
        if not drive_file_id:
            logging.error(f"Drive ID not found for local file {local_file_path}. Cannot delete.")
            return False

        try:
            # Add to ignore list before deleting local file
            self._sync_in_progress_files.add(local_file_path)
            # Pure streaming mode — no local file to delete from disk.
            # Just remove from Drive and mapping.
            with self._drive_api_lock:
                self.drive_service.files().delete(fileId=drive_file_id).execute()

            del self.local_file_info[local_file_path]
            if drive_file_id in self.drive_id_to_local_path:  # Check before deleting
                del self.drive_id_to_local_path[drive_file_id]
            self._save_mapping()
            logging.info(f"Deleted '{os.path.basename(local_file_path)}' (ID: {drive_file_id}) from Drive.")
            return True
        except HttpError as error:
            logging.error(f"Error deleting file {local_file_path} (ID: {drive_file_id}): {error}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error during file deletion {local_file_path}: {e}")
            return False
        finally:
            self._sync_in_progress_files.discard(local_file_path)

    def create_folder(self, local_folder_path):
        """Creates a new folder on Google Drive."""
        if local_folder_path in self.local_file_info:
            logging.warning(f"Folder {local_folder_path} already exists in mapping. Skipping Drive creation.")
            return None  # Return None for consistency

        try:
            folder_name = os.path.basename(local_folder_path)
            # Use the enhanced _get_drive_folder_id to get the parent ID
            parent_id = self._get_drive_folder_id(local_folder_path, is_folder=True)
            if parent_id is None:  # Handle error from _get_drive_folder_id
                logging.error(f"Could not determine parent ID for {local_folder_path}. Skipping folder creation.")
                return None  # Return None for consistency

            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            # Add to ignore list before creating folder on Drive
            self._sync_in_progress_files.add(local_folder_path)

            with self._drive_api_lock:
                folder = (
                    self.drive_service.files()
                    .create(body=file_metadata, fields="id, name, parents, mimeType, size")
                    .execute()
                )

            drive_folder_id = folder.get("id")
            self.local_file_info[local_folder_path] = {
                "id": drive_folder_id,
                "mode": "local",  # Folders are always "local"
                "mimeType": "application/vnd.google-apps.folder",
                "size": "0",
            }
            self.drive_id_to_local_path[drive_folder_id] = local_folder_path
            self._save_mapping()
            logging.info(f"Created folder '{folder_name}' (ID: {drive_folder_id}) on Drive. Local: {local_folder_path}")
            return drive_folder_id
        except HttpError as error:
            logging.error(f"Error creating folder {local_folder_path}: {error}")
            return None
        except Exception as e:
            logging.error(f"Unexpected error during folder creation {local_folder_path}: {e}")
            return None
        finally:
            self._sync_in_progress_files.discard(local_folder_path)

    def delete_folder(self, local_folder_path):
        """Deletes a folder from Google Drive."""
        folder_info = self.local_file_info.get(local_folder_path)

        if not folder_info:
            logging.warning(f"Folder {local_folder_path} not found in mapping for deletion. Skipping Drive deletion.")
            return False  # Return False for consistency

        drive_folder_id = folder_info.get("id")  # Use .get() for safety
        if not drive_folder_id:
            logging.error(f"Drive ID not found for local folder {local_folder_path}. Cannot delete.")
            return False

        try:
            # Add to ignore list before deleting local folder
            self._sync_in_progress_files.add(local_folder_path)
            # Pure streaming mode — no local folder to delete from disk.
            # Just remove from Drive and mapping.
            with self._drive_api_lock:
                self.drive_service.files().delete(fileId=drive_folder_id).execute()
            # Recursively remove all children from mapping as well
            items_to_delete = [lp for lp in self.local_file_info if lp.startswith(local_folder_path)]
            for item_path in items_to_delete:
                item_info = self.local_file_info.get(item_path)
                if item_info and isinstance(item_info, dict):  # Ensure item_info is a dict
                    item_id = item_info.get("id")
                    if item_id:  # Ensure item_id is not None
                        del self.local_file_info[item_path]
                        if item_id in self.drive_id_to_local_path:
                            del self.drive_id_to_local_path[item_id]

            self._save_mapping()
            logging.info(f"Deleted folder '{os.path.basename(local_folder_path)}' (ID: {drive_folder_id}) from Drive.")
            return True
        except HttpError as error:
            logging.error(f"Error deleting folder {local_folder_path} (ID: {drive_folder_id}): {error}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error during folder deletion {local_folder_path}: {e}")
            return False
        finally:
            self._sync_in_progress_files.discard(local_folder_path)

    def move_item(self, src_local_path, dest_local_path):
        """Moves or renames a file/folder on Google Drive."""
        src_file_info = self.local_file_info.get(src_local_path)
        if not src_file_info:
            logging.warning(f"Item {src_local_path} not found in mapping for move/rename. Skipping Drive action.")
            return False  # Return False for consistency

        drive_file_id = src_file_info.get("id")  # Use .get() for safety
        if not drive_file_id:
            logging.error(f"Drive ID not found for local item {src_local_path}. Cannot move/rename.")
            return False

        try:
            # Add both paths to ignore list before local move/rename
            self._sync_in_progress_files.add(src_local_path)
            self._sync_in_progress_files.add(dest_local_path)

            # Determine new name
            new_name = os.path.basename(dest_local_path)

            # Determine if the destination is a folder or file
            is_dest_folder = os.path.isdir(dest_local_path)

            # Determine new parent folder ID. This will also ensure parent folders exist on Drive.
            new_parent_drive_id = self._get_drive_folder_id(dest_local_path, is_folder=is_dest_folder)

            if new_parent_drive_id is None:
                logging.error(f"Could not determine new parent ID for {dest_local_path}. Skipping move/rename.")
                return False  # Return False for consistency

            # Get current parents of the item on Drive
            with self._drive_api_lock:
                current_file_info = self.drive_service.files().get(fileId=drive_file_id, fields="parents").execute()
            old_parents = current_file_info.get("parents", [])
            old_parent_drive_id = old_parents[0] if old_parents else None  # Assuming single parent for simplicity

            # Prepare update body
            update_body = {"name": new_name}
            if new_parent_drive_id and new_parent_drive_id != old_parent_drive_id:
                # Item is being moved to a different parent
                update_body["addParents"] = str(new_parent_drive_id)  # Explicitly cast to str
                if old_parent_drive_id:
                    update_body["removeParents"] = str(old_parent_drive_id)  # Explicitly cast to str

            with self._drive_api_lock:
                self.drive_service.files().update(
                    fileId=drive_file_id, body=update_body, fields="id, name, parents"
                ).execute()

            # In pure streaming mode, no local files exist on disk to move.
            # The mapping update below is sufficient — FUSE will serve the
            # renamed path from the updated mapping.

            # Update mapping for the moved item and its children if it's a folder
            old_info = self.local_file_info.pop(src_local_path)
            self.local_file_info[dest_local_path] = old_info
            self.drive_id_to_local_path[drive_file_id] = dest_local_path

            # If it was a folder, update all children paths in the mapping
            if old_info.get("mimeType") == "application/vnd.google-apps.folder":
                keys_to_update = [k for k in self.local_file_info if k.startswith(src_local_path + os.sep)]
                for old_child_path in keys_to_update:
                    child_info = self.local_file_info.pop(old_child_path)
                    new_child_path = old_child_path.replace(src_local_path, dest_local_path, 1)
                    self.local_file_info[new_child_path] = child_info
                    child_drive_id = child_info.get("id")

                    if child_drive_id:  # Ensure ID exists before using as key
                        self.drive_id_to_local_path[child_drive_id] = new_child_path  # Update reverse mapping too

            self._save_mapping()

            logging.info(f"Moved/Renamed '{src_local_path}' to '{dest_local_path}' (ID: {drive_file_id}) on Drive.")
            return True

        except HttpError as error:
            logging.error(f"Error moving/renaming item {src_local_path} to {dest_local_path}: {error}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error during move/rename {src_local_path} to {dest_local_path}: {e}")
            return False
        finally:
            self._sync_in_progress_files.discard(src_local_path)
            self._sync_in_progress_files.discard(dest_local_path)

    def download_file(self, drive_file_id, local_file_path):
        """Downloads a file from Google Drive to the local cache.

        *local_file_path* is the destination path. When using FUSE, this
        should be a path inside FUSE_CACHE_DIR (typically the cache path).

        Uses a per-thread service from the pool so multiple concurrent
        downloads proceed in parallel.
        """
        try:
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            # Add to ignore list before writing
            self._sync_in_progress_files.add(local_file_path)

            svc = self._get_service()
            request = svc.files().get_media(fileId=drive_file_id)
            fh = io.FileIO(local_file_path, "wb")
            downloader = MediaIoBaseDownload(fh, request)
            done = False

            while done is False:
                status, done = downloader.next_chunk()
                logging.debug(
                    f"Download progress for {os.path.basename(local_file_path)}: {int(status.progress() * 100)}%."
                )
            fh.close()

            logging.info(f"Downloaded Drive file (ID: {drive_file_id}) to {local_file_path}")
            return True
        except HttpError as error:
            logging.error(f"Error downloading file (ID: {drive_file_id}) to {local_file_path}: {error}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error during file download (ID: {drive_file_id}) to {local_file_path}: {e}")
            return False
        finally:
            self._sync_in_progress_files.discard(local_file_path)

    def initial_sync_from_drive(self):
        """
        Performs an initial synchronization, downloading all files and folders from Google Drive
        to the local sync folder and populating the mapping.
        """
        logging.info("Starting initial sync from Google Drive...")

        # Get the current startPageToken before processing changes
        try:
            svc = self._get_service()
            response = svc.changes().getStartPageToken().execute()
            self.last_change_token = response.get("startPageToken")
            logging.info(f"Initial startPageToken obtained: {self.last_change_token}")
        except HttpError as error:
            logging.error(f"Error getting startPageToken for initial sync: {error}")
            return

        # Clear existing mapping for a fresh sync (optional, but good for initial setup)
        self.local_file_info = {}
        self.drive_id_to_local_path = {}
        # self.last_change_token is already set above
        self._save_mapping()  # Save empty mapping with new token

        # Dictionary to map Drive folder IDs to their local paths
        drive_id_to_local_folder_path = {"root": config.LOCAL_SYNC_FOLDER}

        # List all files and folders from Drive
        query = "trashed = false"  # Only get non-trashed items
        fields = "nextPageToken, files(id, name, mimeType, parents, size)"  # Request size and mimeType

        page_token = None
        while True:
            try:
                svc = self._get_service()
                response = svc.files().list(q=query, spaces="drive", fields=fields, pageToken=page_token).execute()

                for item in response.get("files", []):
                    drive_id = item["id"]
                    name = item["name"]
                    mime_type = item["mimeType"]
                    parents = item.get("parents", [])
                    size = item.get("size", "0")  # Get size, default to '0' for folders/unknown

                    # Determine parent local path
                    parent_drive_id = parents[0] if parents else "root"  # Assume single parent for simplicity
                    local_parent_path = drive_id_to_local_folder_path.get(parent_drive_id)

                    if not local_parent_path:
                        logging.warning(
                            f"Skipping item '{name}' (ID: {drive_id}) due to unknown parent ID: {parent_drive_id}"
                        )
                        continue

                    local_path = os.path.join(local_parent_path, name)

                    try:
                        self._sync_in_progress_files.add(local_path)  # Add to ignore list

                        if mime_type == "application/vnd.google-apps.folder":
                            # Folder — pure streaming mode: no real directories created.
                            # FUSE will serve directory listings via readdir() using
                            # the mapping metadata. No os.makedirs() here.
                            self.local_file_info[local_path] = {
                                "id": drive_id,
                                "mode": "local",  # Folders are always "local"
                                "mimeType": mime_type,
                                "size": size,
                            }
                            self.drive_id_to_local_path[drive_id] = local_path
                            drive_id_to_local_folder_path[drive_id] = local_path  # Add to folder path mapping
                        else:
                            # It's a file — with FUSE, just populate the mapping.
                            # No placeholder files are created on disk.
                            self.local_file_info[local_path] = {
                                "id": drive_id,
                                "mode": DEFAULT_FILE_MODE,
                                "mimeType": mime_type,
                                "size": size,
                                "last_accessed_time": 0,  # Not accessed yet
                            }
                            self.drive_id_to_local_path[drive_id] = local_path
                    finally:
                        self._sync_in_progress_files.discard(local_path)

                # Batch-save after processing all items in this page
                self._save_mapping()

                page_token = response.get("nextPageToken", None)
                if not page_token:
                    break
            except HttpError as error:
                logging.error(f"An error occurred during initial sync: {error}")
                break
            except Exception as e:
                logging.error(f"Unexpected error during initial sync: {e}")
                break

        logging.info("Initial sync from Google Drive completed.")

    def sync_from_remote(self):
        """
        Checks Google Drive for changes since the last sync token and applies them locally.
        """
        if not self.last_change_token:
            logging.warning("No last_change_token found. Performing initial sync instead.")
            self.initial_sync_from_drive()
            return

        logging.info(f"Checking for remote changes from token: {self.last_change_token}")

        page_token = self.last_change_token
        while True:
            try:
                svc = self._get_service()
                response = (
                    svc.changes()
                    .list(
                        pageToken=page_token,
                        spaces="drive",
                        fields=(
                            "nextPageToken, newStartPageToken, "
                            "changes(fileId, file(id, name, mimeType, "
                            "parents, trashed, size))"
                        ),
                        # Request size
                    )
                    .execute()
                )

                # Update the last_change_token immediately after a successful response
                self.last_change_token = response.get("newStartPageToken", self.last_change_token)
                self._save_mapping()  # Save mapping with the new token

                for change in response.get("changes", []):
                    file_id = change["fileId"]
                    drive_file = change.get("file")  # This will be None if the file is deleted

                    local_path = self.drive_id_to_local_path.get(file_id)
                    new_local_path = local_path  # Initialize new_local_path for finally block

                    # Add local_path to ignore list while processing this change
                    if local_path:
                        self._sync_in_progress_files.add(local_path)

                    try:
                        if drive_file and not drive_file["trashed"]:
                            # Item exists and is not trashed (created, updated, moved/renamed)
                            name = drive_file["name"]
                            mime_type = drive_file["mimeType"]
                            parents = drive_file.get("parents", [])
                            size = drive_file.get("size", "0")
                            parent_drive_id = parents[0] if parents else "root"

                            # Determine the new local path based on its Drive parent
                            local_parent_path = self.drive_id_to_local_path.get(parent_drive_id)

                            if not local_parent_path:
                                # If parent is not mapped, it means it's a new folder or a folder not yet synced
                                # For simplicity, we'll assume 'root' if parent is unknown for now.
                                # A more robust solution would ensure parent folders are created first.
                                local_parent_path = config.LOCAL_SYNC_FOLDER
                                logging.warning(
                                    f"Remote change: Parent for {name} (ID: {file_id})"
                                    " not found in local mapping. Assuming root."
                                )

                            new_local_path = os.path.join(local_parent_path, name)

                            # If the item is being moved/renamed, add the new path to ignore list too
                            if new_local_path and new_local_path != local_path:  # Check new_local_path is not None
                                self._sync_in_progress_files.add(new_local_path)

                            if mime_type == "application/vnd.google-apps.folder":
                                # Folder created/moved/renamed — pure streaming mode:
                                # No real directories created on disk. FUSE serves
                                # directory listings via readdir() using the mapping.

                                # Update mapping — only metadata changes
                                if local_path and local_path != new_local_path:  # Renamed/Moved
                                    # Remove old mapping entries for the folder and its children
                                    items_to_delete = [
                                        lp
                                        for lp in self.local_file_info
                                        if lp == local_path or lp.startswith(local_path + os.sep)
                                    ]
                                    for item_path in items_to_delete:
                                        item_info = self.local_file_info.pop(item_path, None)
                                        if item_info and isinstance(item_info, dict):
                                            item_id = item_info.get("id")
                                            if item_id and item_id in self.drive_id_to_local_path:
                                                del self.drive_id_to_local_path[item_id]

                                self.local_file_info[new_local_path] = {
                                    "id": file_id,
                                    "mode": "local",  # Folders are always local
                                    "mimeType": mime_type,
                                    "size": size,
                                }
                                self.drive_id_to_local_path[file_id] = new_local_path
                                self._save_mapping()

                            else:
                                # File created/updated/moved/renamed
                                # In pure streaming mode, we never have local file content,
                                # so there's no conflict detection — just update metadata.
                                if local_path and local_path != new_local_path:  # Renamed/Moved
                                    # Pure streaming mode — no local file to remove.
                                    # Just update the mapping.
                                    del self.local_file_info[local_path]
                                    logging.info(
                                        f"Remote: Renamed/Moved local file mapping"
                                        f" from {local_path} to {new_local_path}"
                                    )

                                # In pure streaming mode, we never download content to disk.
                                # All files are always "streaming" — update metadata only.
                                logging.info(f"Remote: Updated metadata for file '{name}' (ID: {file_id})")
                                # Update mapping with latest size/mimeType from Drive
                                self.local_file_info[new_local_path] = {
                                    "id": file_id,
                                    "mode": DEFAULT_FILE_MODE,
                                    "mimeType": mime_type,
                                    "size": size,
                                    "last_accessed_time": 0,
                                }
                                self.drive_id_to_local_path[file_id] = new_local_path
                                self._save_mapping()

                        else:  # drive_file is None or drive_file['trashed'] is True
                            # Item deleted or trashed on Drive
                            removed_from_mapping = False
                            if local_path and local_path in self.local_file_info:
                                del self.local_file_info[local_path]
                                removed_from_mapping = True
                            if file_id in self.drive_id_to_local_path:
                                del self.drive_id_to_local_path[file_id]
                                removed_from_mapping = True

                            if removed_from_mapping:
                                self._save_mapping()
                                logging.info(
                                    "Remote: Deleted local item '%s' (ID: %s) due to Drive change.",
                                    local_path,
                                    file_id,
                                )
                            else:
                                logging.info(
                                    (
                                        "Remote: Item (ID: %s) deleted on Drive,"
                                        " but not found locally or already unmapped."
                                    ),
                                    file_id,
                                )
                    finally:
                        if local_path:
                            self._sync_in_progress_files.discard(local_path)
                        if new_local_path and new_local_path != local_path:  # If it was a move/rename
                            self._sync_in_progress_files.discard(new_local_path)

                page_token = response.get("nextPageToken", None)
                if not page_token:
                    break
            except HttpError as error:
                logging.error(f"An error occurred during remote sync: {error}")
                break
            except Exception:
                logging.exception("Unexpected error during remote sync")
                break

        logging.info(f"Remote sync completed. New change token: {self.last_change_token}")

    def set_file_mode(self, local_path, new_mode):
        """
        Deprecated in pure streaming mode. All files are always streamed
        — there is no local content to control. Kept as a no-op stub for
        backward compatibility with any callers.
        """
        if new_mode not in ["local", "online"]:
            logging.error(f"Invalid file mode: {new_mode}. Must be 'local' or 'online'.")
            return False

        file_info = self.local_file_info.get(local_path)
        if not file_info or not isinstance(file_info, dict):  # Ensure file_info is a dict
            logging.warning(f"Cannot set mode for non-existent item or invalid info: {local_path}")
            return False
        if file_info.get("mimeType") == "application/vnd.google-apps.folder":
            logging.warning(f"Cannot set mode for a folder: {local_path}")
            return False

        logging.info(f"set_file_mode({local_path}, '{new_mode}') called — no-op in pure streaming mode.")
        return True

    def update_last_accessed_time(self, local_path):
        """Updates the last accessed time for a file in the mapping."""
        file_info = self.local_file_info.get(local_path)
        if file_info and isinstance(file_info, dict):
            file_info["last_accessed_time"] = time.time()
            self._save_mapping()
            logging.debug("Updated last accessed time for %s", local_path)

    def _remove_from_mapping(self, local_path):
        """Remove a single file from the mapping without touching Drive."""
        file_info = self.local_file_info.pop(local_path, None)
        if file_info and isinstance(file_info, dict):
            drive_id = file_info.get("id")
            if drive_id and drive_id in self.drive_id_to_local_path:
                del self.drive_id_to_local_path[drive_id]
            self._save_mapping()
            logging.info("Removed '%s' from mapping (Drive file kept).", local_path)

    def _remove_folder_from_mapping(self, local_path):
        """Remove a folder and all its children from mapping without touching Drive."""
        # Remove children first
        keys_to_delete = [k for k in self.local_file_info if k.startswith(local_path + os.sep)]
        for key in keys_to_delete:
            info = self.local_file_info.pop(key, None)
            if info and isinstance(info, dict):
                drive_id = info.get("id")
                if drive_id and drive_id in self.drive_id_to_local_path:
                    del self.drive_id_to_local_path[drive_id]
        # Remove the folder itself
        folder_info = self.local_file_info.pop(local_path, None)
        if folder_info and isinstance(folder_info, dict):
            drive_id = folder_info.get("id")
            if drive_id and drive_id in self.drive_id_to_local_path:
                del self.drive_id_to_local_path[drive_id]
        self._save_mapping()
        logging.info("Removed '%s' and children from mapping (Drive folder kept).", local_path)
