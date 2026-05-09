"""gdrive-linux FUSE filesystem — pure streaming network drive.

Mounts a virtual filesystem at FUSE_MOUNT_POINT that presents Google Drive
files as regular files. Nothing is stored on disk — read() fetches only the
requested byte range from the Drive API via HTTP Range headers, and write()
accumulates content in memory and uploads on close(). True zero-local-storage
network drive.

A local disk cache (``~/.cache/gdrive-linux/cache/``) stores recently-accessed
file chunks for fast re-reads. The cache uses LRU eviction and is pruned by a
background cleanup thread.

Local file structure:
  FUSE_MOUNT_POINT/   (virtual — served by FUSE, nothing on disk)
"""

import os
import io
import time
import errno
import logging
import threading

from fuse import Operations, FuseOSError

import config
from config import (
    FUSE_CACHE_DIR,
    CACHE_CHUNK_SIZE,
    READAHEAD_WINDOW_CHUNKS,
    MAX_CONCURRENT_FETCHES,
    PREFETCH_TRIGGER_THRESHOLD,
)

from disk_cache import (
    get_chunk,
    has_chunk,
    put_chunk,
    invalidate_file,
    CacheCleanupThread,
)

log = logging.getLogger(__name__)

# Google Docs export cache (per file handle).
# Google Docs files must be exported in full (no Range header support), so the
# export result is cached in memory for the lifetime of the open file handle.
# Regular binary files are never cached — each read() is an independent ranged
# HTTP request with no memory overhead.
# Mapping: file_handle -> io.BytesIO
_stream_buffers: dict[int, io.BytesIO] = {}
_stream_buffers_lock = threading.Lock()
_next_fh = 1
_next_fh_lock = threading.Lock()

# Per-file dirty buffers for writes (only tracks modified files).
# Mapping: file_handle -> io.BytesIO of accumulated write content
_dirty_buffers: dict[int, io.BytesIO] = {}
_dirty_fh_to_path: dict[int, str] = {}


def _allocate_fh() -> int:
    global _next_fh
    with _next_fh_lock:
        fh = _next_fh
        _next_fh += 1
        return fh


# MIME types that are Google Workspace native formats (not binary downloadables).
# These files must be exported rather than downloaded via get_media().
_GOOGLE_DOCS_MIMES: set[str] = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.drawing",
    "application/vnd.google-apps.script",
    "application/vnd.google-apps.form",
    "application/vnd.google-apps.fusiontable",
    "application/vnd.google-apps.jam",
    "application/vnd.google-apps.map",
    "application/vnd.google-apps.site",
}

# Default export MIME types for Google Workspace files.
_GOOGLE_DOCS_EXPORT_FORMATS: dict[str, str] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    "application/vnd.google-apps.drawing": "image/png",
    "application/vnd.google-apps.script": "application/vnd.google-apps.script+json",
    "application/vnd.google-apps.form": "application/zip",
    "application/vnd.google-apps.fusiontable": (
        "application/vnd.google-apps.fusiontable+json"
    ),
    "application/vnd.google-apps.jam": "application/pdf",
    "application/vnd.google-apps.map": "application/vnd.google-apps.map+json",
    "application/vnd.google-apps.site": "text/plain",
}


def _stream_from_drive(
    drive_service,
    file_id: str,
    mime_type: str,
    offset: int = 0,
    size: int | None = None,
    populate_cache: bool = False,
    service_pool=None,
) -> io.BytesIO:
    """Download a byte range of file content from Google Drive.

    For regular binary files, uses HTTP Range headers to fetch only the
    requested bytes (``offset`` to ``offset + size``). For Google Docs
    (Workspace) files, the export result is always fetched in full and
    the requested range is sliced from it.

    If *populate_cache* is True, the downloaded range is also written to
    the local disk cache (``~/.cache/gdrive-linux/cache/``) for faster
    re-reads.

    If *service_pool* is provided, the per-thread service from the pool
    is used for the HTTP request — allowing concurrent FUSE read threads
    to fetch different chunks in parallel without lock contention.
    Falls back to *drive_service* if no pool is given.

    Returns a BytesIO buffer positioned at 0 containing only the
    requested range.
    """
    from googleapiclient.http import MediaIoBaseDownload

    # Use a per-thread service from the pool if available — this is the
    # key to parallel reads since each thread gets its own HTTP connection.
    if service_pool is not None:
        svc = service_pool.get()
    else:
        svc = drive_service
    if svc is None:
        svc = drive_service

    buf = io.BytesIO()

    if mime_type in _GOOGLE_DOCS_MIMES:
        # Google Docs export must be fetched in full; range-slice afterwards.
        export_mime = _GOOGLE_DOCS_EXPORT_FORMATS.get(mime_type, "application/pdf")
        request = svc.files().export(fileId=file_id, mimeType=export_mime)
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(offset)
        data = buf.read(size) if size is not None else buf.read()
        return io.BytesIO(data)

    # Regular file — use a direct HTTP request with Range header.
    # IMPORTANT: We cannot use MediaIoBaseDownload with a manually-set Range
    # header because the google client library may override or ignore headers
    # set after request creation. Instead, we execute the request directly
    # via the underlying HTTP transport, which reliably sends the Range header.
    request = svc.files().get_media(fileId=file_id)

    if size is not None and size > 0:
        # Use the authorized HTTP from google client to make a direct
        # request with Range header. This is more reliable than setting
        # headers on an HttpRequest and using MediaIoBaseDownload, which
        # may override custom headers.
        uri = request.uri
        http = request.http

        resp, content = http.request(
            uri,
            method="GET",
            headers={"Range": f"bytes={offset}-{offset + size - 1}"},
        )

        if resp.status == 206:
            # Partial Content — server respected our Range header
            buf.write(content)
        elif resp.status == 200:
            # Server ignored Range header and returned full content.
            # Slice out just what we need.
            log.debug(
                "Server returned full content (200) instead of partial (206) "
                "for Range request. Slicing %d bytes at offset %d.",
                min(size, len(content) - offset),
                offset,
            )
            buf.write(content[offset : offset + size])
        elif resp.status == 416:
            # Range Not Satisfiable — file is smaller than requested range
            log.warning(
                "Range not satisfiable for %s: offset=%d size=%d (file size may have changed).",
                file_id,
                offset,
                size,
            )
            # Try fetching from offset to end
            resp, content = http.request(
                uri,
                method="GET",
                headers={"Range": f"bytes={offset}-"},
            )
            if resp.status in (200, 206):
                buf.write(content)
            else:
                raise IOError(f"HTTP {resp.status}: {resp.reason}")
        else:
            raise IOError(f"HTTP {resp.status}: {resp.reason}")
    else:
        # No Range header — download the full file
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            try:
                _, done = downloader.next_chunk()
            except Exception as e:
                data = buf.getvalue()
                if data:
                    log.debug(
                        "Partial download: got %d bytes before error: %s",
                        len(data),
                        e,
                    )
                    done = True
                    break
                raise

    data = buf.getvalue()

    # Verify we got the requested amount (when using Range header).
    # Some APIs silently truncate large Range requests.
    if size is not None and size > 0 and len(data) < size:
        log.debug(
            "Range request truncated: requested %d bytes, got %d bytes "
            "(offset=%d). This is normal near end-of-file or if the API "
            "has a per-request limit.",
            size,
            len(data),
            offset,
        )

    # Optionally populate cache for this chunk
    if populate_cache and size is not None and size > 0:
        chunk_index = offset // CACHE_CHUNK_SIZE
        put_chunk(file_id, chunk_index, data)

    buf.seek(0)
    return buf


class DriveFS(Operations):
    """FUSE operations backed by SyncManager metadata with optional disk cache.

    File content is served from a local disk cache
    (``~/.cache/gdrive-linux/cache/``) when available. On cache miss, content
    is fetched from the Google Drive API via HTTP Range headers and written
    to the cache for future access. Writes are accumulated in memory and
    uploaded to Drive on release().

    A background thread (``CacheCleanupThread``) periodically evicts stale
    chunks using LRU policy to keep the cache under the configured size limit.

    Delete operations (unlink, rmdir) for Drive-original files are blocked
    and emit a notification via the *notify_callback* to warn the user that
    deletion is permanent.

    Usage:
        fs = DriveFS(sync_manager, notify_callback=my_notify_func)
        server = FUSE(fs, mount_point, foreground=True)
    """

    def __init__(self, sync_manager, notify_callback=None, stop_event=None):
        self.sm = sync_manager
        # Reference to the thread-local service pool for parallel reads.
        # Passed to _stream_from_drive so each FUSE read thread gets its own
        # Drive service instance with an independent HTTP connection pool.
        self._service_pool = getattr(sync_manager, "_service_pool", None)
        # Track file paths that have had write() called (for upload on release)
        self._modified_paths: set[str] = set()
        # Callback for showing notifications (e.g., tray messages)
        self._notify = notify_callback or (lambda msg: None)
        # Semaphore limiting concurrent Drive API window fetches.
        # At most MAX_CONCURRENT_FETCHES (default 3) threads can download
        # simultaneously. Additional readers block until a slot frees up.
        # Each fetch takes ~0.5-1.5s, so wait times are short and no
        # video player will receive empty bytes (which would be interpreted as EOF).
        self._fetch_semaphore = threading.Semaphore(MAX_CONCURRENT_FETCHES)

        # Background pre-fetch tracking.
        # When a sequential read gets close to the end of cached data, we
        # launch a background thread to pre-fetch the next window. This
        # avoids blocking the FUSE read thread when the player reaches it.
        # Maps: drive_id -> set of chunk indices that are being pre-fetched
        self._prefetch_in_progress: dict[str, set[int]] = {}
        self._prefetch_lock = threading.Lock()
        # Track which file is currently being read sequentially (for pre-fetch).
        # Maps: drive_id -> last chunk_index read
        self._last_read_chunk: dict[str, int] = {}

        # Start background cache cleanup thread
        self._cache_stop_event = stop_event or threading.Event()
        self._cache_cleanup = CacheCleanupThread(self._cache_stop_event)
        self._cache_cleanup.start()

    # ---- Helpers ----

    def _info(self, path: str) -> dict | None:
        """Return the mapping dict for *path*, or None."""
        assert config.LOCAL_SYNC_FOLDER is not None, (
            "Sync folder must be set before FUSE operations"
        )
        full = os.path.join(config.LOCAL_SYNC_FOLDER, path.lstrip("/"))
        with self.sm._mapping_lock:
            return self.sm.local_file_info.get(full)

    def _full_path(self, path: str) -> str:
        """Convert a FUSE path (relative to mount) to an absolute local path."""
        assert config.LOCAL_SYNC_FOLDER is not None, (
            "Sync folder must be set before FUSE operations"
        )
        return os.path.join(config.LOCAL_SYNC_FOLDER, path.lstrip("/"))

    # ---- Filesystem API ----

    def getattr(self, path, fh=None):
        """Return stat-like dict for *path* — purely from mapping metadata."""
        info = self._info(path)

        if info is None and path != "/":
            raise FuseOSError(errno.ENOENT)

        now = time.time()
        st = {
            "st_atime": now,
            "st_mtime": now,
            "st_ctime": now,
            "st_uid": os.getuid(),
            "st_gid": os.getgid(),
        }

        if path == "/":
            st["st_mode"] = 0o40755
            st["st_nlink"] = 2
            st["st_size"] = 4096
            return st

        if info is None:
            raise FuseOSError(errno.ENOENT)

        mime = info.get("mimeType", "")

        if mime == "application/vnd.google-apps.folder":
            st["st_mode"] = 0o40755
            st["st_nlink"] = 2
            st["st_size"] = 4096
        else:
            st["st_mode"] = 0o100644
            st["st_nlink"] = 1
            try:
                st["st_size"] = int(info.get("size", 0))
            except (ValueError, TypeError):
                st["st_size"] = 0

        return st

    def readdir(self, path, fh):
        """List directory contents from the mapping."""
        full_parent = self._full_path(path).rstrip("/")
        entries = [".", ".."]

        with self.sm._mapping_lock:
            for mapped_path in self.sm.local_file_info:
                if mapped_path == full_parent:
                    continue
                if mapped_path.startswith(full_parent + os.sep):
                    remainder = mapped_path[len(full_parent) + 1 :]
                    if os.sep not in remainder:
                        entries.append(remainder)

        return entries

    def statfs(self, path):
        """Return filesystem stat info.

        Reports space from the root filesystem since we store nothing locally.
        """
        try:
            stv = os.statvfs(FUSE_CACHE_DIR)
        except OSError:
            stv = os.statvfs("/")
        return {
            "f_bsize": stv.f_bsize,
            "f_frsize": stv.f_frsize,
            "f_blocks": stv.f_blocks,
            "f_bfree": stv.f_bfree,
            "f_bavail": stv.f_bavail,
            "f_files": stv.f_files,
            "f_ffree": stv.f_ffree,
            "f_favail": stv.f_favail,
        }

    # ---- File open / read ----

    def open(self, path, flags):
        """Open a file.

        No data is fetched here — we just verify the file exists and
        allocate a file handle. Streaming content fetches happen in read().
        """
        if self._cache_stop_event.is_set():
            raise FuseOSError(errno.EIO)

        info = self._info(path)
        if info is None:
            raise FuseOSError(errno.ENOENT)

        if info.get("mimeType") == "application/vnd.google-apps.folder":
            raise FuseOSError(errno.EISDIR)

        drive_id = info.get("id")
        if not drive_id:
            raise FuseOSError(errno.EIO)

        return _allocate_fh()

    def read(self, path, size, offset, fh):
        """Read *size* bytes from *offset* by streaming from the Drive API.

        Checks the local disk cache first. On cache miss, tries to acquire
        a semaphore slot (``MAX_CONCURRENT_FETCHES`` = 3). If a slot is
        available, fetches a window of ``READAHEAD_WINDOW_CHUNKS`` (default
        4, i.e. 16 MB) around the requested offset in one HTTP Range request
        and caches all contained 4 MB chunks.

        If no semaphore slot is available within 3 seconds, returns empty
        data immediately — the video player will retry the read. This
        prevents deadlocks when multiple concurrent seeks compete for slots.

        Google Docs files are cached in memory (per file handle) since
        they must be exported in full.

        The on-disk cache stores 4 MB chunks under
        ``~/.cache/gdrive-linux/cache/<drive_id>/`` with LRU eviction
        managed by a background cleanup thread.
        """
        # If shutdown is in progress, return empty data immediately rather
        # than attempting a network call that will fail or segfault.
        if self._cache_stop_event.is_set():
            log.debug("Shutdown in progress — returning empty read for '%s'.", path)
            return b""

        info = self._info(path)
        if info is None:
            raise FuseOSError(errno.ENOENT)

        drive_id = info.get("id")
        if not drive_id:
            raise FuseOSError(errno.EIO)

        file_size = int(info.get("size", 0))
        mime_type = info.get("mimeType", "")
        is_doc = mime_type in _GOOGLE_DOCS_MIMES

        if is_doc:
            # Google Docs must be exported in full — cache the export per handle
            with _stream_buffers_lock:
                buf = _stream_buffers.get(fh)
            if buf is None:
                log.info(
                    "Exporting Google Doc '%s' (ID: %s) (fh=%d)...",
                    path,
                    drive_id,
                    fh,
                )
                try:
                    buf = _stream_from_drive(
                        self.sm.drive_service,
                        drive_id,
                        mime_type,
                        service_pool=self._service_pool,
                    )
                except Exception as e:
                    log.error("Failed to export Google Doc '%s': %s", path, e)
                    with _stream_buffers_lock:
                        _stream_buffers.pop(fh, None)
                    raise FuseOSError(errno.EIO) from e
                with _stream_buffers_lock:
                    _stream_buffers[fh] = buf
                log.info(
                    "Exported Google Doc '%s' into memory (%d bytes).",
                    path,
                    buf.getbuffer().nbytes,
                )
            buf.seek(offset)
            return buf.read(size)

        # Regular binary file — read from disk cache
        chunk_index = offset // CACHE_CHUNK_SIZE
        chunk_offset = offset % CACHE_CHUNK_SIZE

        # Check if the requested data is entirely within a single cached chunk
        cached = get_chunk(drive_id, chunk_index)
        if cached is not None:
            end_offset = chunk_offset + size
            if end_offset <= len(cached):
                log.debug(
                    "Cache HIT '%s' chunk=%d offset=%d size=%d",
                    path,
                    chunk_index,
                    chunk_offset,
                    size,
                )
                # Trigger pre-fetch of next chunk if we've read past the threshold
                # within this chunk. This gives the background fetch time to
                # complete before the player needs the next chunk.
                try:
                    if chunk_offset + size >= len(cached) * PREFETCH_TRIGGER_THRESHOLD:
                        self._trigger_prefetch(path, drive_id, file_size, chunk_index)
                except Exception:
                    log.debug("Prefetch trigger failed (non-fatal)", exc_info=True)
                return cached[chunk_offset:end_offset]
            else:
                # Request spans beyond this cached chunk — we need the next chunk too.
                # Fall through to fetch logic below. But first, serve what we can
                # from cache so the semaphore slot isn't wasted on a partial hit.
                log.debug(
                    "Cache HIT (partial) '%s' chunk=%d — read spans beyond chunk boundary, "
                    "falling through to fetch next chunk",
                    path,
                    chunk_index,
                )
                # Return what we have from cache — the caller (kernel) will re-read
                # the rest. This prevents short reads from confusing video players.
                partial = cached[chunk_offset:]
                if len(partial) > 0:
                    return partial
                # If cached chunk is somehow empty, fall through to fetch.

        # Cache miss — acquire a semaphore slot to limit concurrent fetches.
        # Block indefinitely (no timeout) because returning empty bytes would
        # cause video players to interpret it as EOF and reset playback to 00:00.
        # The semaphore limits to MAX_CONCURRENT_FETCHES (default 3), so at most
        # 3 threads will block here, and each fetch takes ~0.5-1.5s.
        log.debug(
            "Cache MISS '%s' chunk=%d (offset=%d, size=%d) — waiting for fetch slot...",
            path,
            chunk_index,
            chunk_offset,
            size,
        )
        self._fetch_semaphore.acquire()

        try:
            result = self._read_with_readahead(
                path, drive_id, file_size, chunk_index, chunk_offset, size
            )
            if len(result) == 0:
                log.info(
                    "Read '%s' offset=%d size=%d -> 0 bytes (EOF or past EOF)",
                    path,
                    offset,
                    size,
                )
            # Trigger pre-fetch of the next chunk (the read-ahead already
            # did this, but this ensures it happens even for cache-already paths)
            try:
                self._trigger_prefetch(path, drive_id, file_size, chunk_index)
            except Exception:
                log.debug("Prefetch trigger failed (non-fatal)", exc_info=True)
            return result
        finally:
            self._fetch_semaphore.release()

    def _trigger_prefetch(
        self,
        path: str,
        drive_id: str,
        file_size: int,
        just_read_chunk: int,
    ):
        """Launch a background thread to pre-fetch the next window."""
        if file_size <= 0:
            return
        total_chunks = (file_size + CACHE_CHUNK_SIZE - 1) // CACHE_CHUNK_SIZE
        next_chunk = just_read_chunk + 1
        if next_chunk >= total_chunks:
            return
        # Check if already cached or already being pre-fetched
        if has_chunk(drive_id, next_chunk):
            return
        with self._prefetch_lock:
            if drive_id in self._prefetch_in_progress:
                if next_chunk in self._prefetch_in_progress[drive_id]:
                    return
                self._prefetch_in_progress[drive_id].add(next_chunk)
            else:
                self._prefetch_in_progress[drive_id] = {next_chunk}

        # Launch background pre-fetch thread
        t = threading.Thread(
            target=self._prefetch_worker,
            args=(path, drive_id, file_size, next_chunk),
            daemon=True,
        )
        t.start()

    def _prefetch_worker(
        self,
        path: str,
        drive_id: str,
        file_size: int,
        chunk_index: int,
    ):
        """Background thread: pre-fetch a window starting at chunk_index."""
        try:
            # Clean up tracking when done
            try:
                if not self._fetch_semaphore.acquire(timeout=5):
                    log.debug(
                        "Prefetch timed out waiting for slot for '%s' chunk %d.",
                        path,
                        chunk_index,
                    )
                    return
            except (ValueError, AttributeError):
                return

            try:
                # Use the same read-ahead logic but fetch starting from this chunk
                self._do_background_fetch(path, drive_id, file_size, chunk_index)
            finally:
                self._fetch_semaphore.release()
        finally:
            with self._prefetch_lock:
                if drive_id in self._prefetch_in_progress:
                    self._prefetch_in_progress[drive_id].discard(chunk_index)
                    if not self._prefetch_in_progress[drive_id]:
                        del self._prefetch_in_progress[drive_id]

    def _do_background_fetch(
        self,
        path: str,
        drive_id: str,
        file_size: int,
        chunk_index: int,
    ):
        """Fetch a window for pre-fetching and cache it."""
        total_chunks = (file_size + CACHE_CHUNK_SIZE - 1) // CACHE_CHUNK_SIZE
        start_chunk = chunk_index
        end_chunk = min(total_chunks, start_chunk + READAHEAD_WINDOW_CHUNKS)
        # Clamp to file size
        window_offset = start_chunk * CACHE_CHUNK_SIZE
        window_size = (end_chunk - start_chunk) * CACHE_CHUNK_SIZE
        if window_offset + window_size > file_size:
            window_size = file_size - window_offset
            if window_size <= 0:
                return
        try:
            buf = _stream_from_drive(
                self.sm.drive_service,
                drive_id,
                "",
                offset=window_offset,
                size=window_size,
                populate_cache=False,
                service_pool=self._service_pool,
            )
            window_data = buf.read()
        except Exception as e:
            log.debug("Prefetch failed for '%s' chunk %d: %s", path, chunk_index, e)
            return
        actual_size = len(window_data)
        num_chunks = (actual_size + CACHE_CHUNK_SIZE - 1) // CACHE_CHUNK_SIZE
        for i in range(num_chunks):
            cs = i * CACHE_CHUNK_SIZE
            ce = min(cs + CACHE_CHUNK_SIZE, actual_size)
            if ce > cs:
                chunk_data = window_data[cs:ce]
                if len(chunk_data) > 0:
                    put_chunk(drive_id, start_chunk + i, chunk_data)
        log.info(
            "Prefetched %d-chunk window for '%s' (chunks %d-%d) in background.",
            end_chunk - start_chunk,
            path,
            start_chunk,
            end_chunk - 1,
        )

    def _read_with_readahead(
        self,
        path: str,
        drive_id: str,
        file_size: int,
        chunk_index: int,
        chunk_offset: int,
        size: int,
    ) -> bytes:
        """Fetch a window around the requested chunk and cache it.

        Strategy (solves video seek bandwidth/memory problem):
        Fetch a single larger window (READAHEAD_WINDOW_CHUNKS * 4MB = 32 MB
        by default) around the seek position in one HTTP Range request.
        Write all contained 4 MB chunks to disk cache.

        Why this works for video seeking:
        - The 100 MB/s bandwidth spike was caused by MULTIPLE overlapping
          16 MB windows from concurrent seeks. With a semaphore limiting
          to 3 concurrent fetches, at most 3 windows (96 MB total) can be
          in-flight, and they're for different files — not overlapping.
        - A 32 MB window at 100 Mbps takes ~2.5 seconds to download, during
          which the video player can read from cache without further network
          calls.
        - The window is aligned to chunk boundaries so no overlaps occur
          within the same file.
        """
        total_chunks = (file_size + CACHE_CHUNK_SIZE - 1) // CACHE_CHUNK_SIZE

        if READAHEAD_WINDOW_CHUNKS <= 0:
            # Windowed fetching disabled — fetch just the requested chunk
            urgent_offset = chunk_index * CACHE_CHUNK_SIZE
            urgent_size = min(CACHE_CHUNK_SIZE, file_size - urgent_offset)
            try:
                buf = _stream_from_drive(
                    self.sm.drive_service,
                    drive_id,
                    "",
                    offset=urgent_offset,
                    size=urgent_size,
                    populate_cache=True,
                    service_pool=self._service_pool,
                )
                chunk_data = buf.read()
            except Exception as e:
                log.error("Failed to read '%s' from Drive: %s", path, e)
                raise FuseOSError(errno.EIO) from e
            if chunk_offset + size <= len(chunk_data):
                return chunk_data[chunk_offset : chunk_offset + size]
            return chunk_data[chunk_offset:]

        # Calculate the window: centered on the requested chunk, aligned
        # to chunk boundaries.
        half_window = READAHEAD_WINDOW_CHUNKS // 2
        start_chunk = max(0, chunk_index - half_window)
        end_chunk = min(total_chunks, start_chunk + READAHEAD_WINDOW_CHUNKS)

        # If we're near the end, shift the window left so we still get
        # READAHEAD_WINDOW_CHUNKS worth of data (if available).
        if end_chunk - start_chunk < READAHEAD_WINDOW_CHUNKS and start_chunk > 0:
            start_chunk = max(0, end_chunk - READAHEAD_WINDOW_CHUNKS)

        window_offset = start_chunk * CACHE_CHUNK_SIZE
        window_size = (end_chunk - start_chunk) * CACHE_CHUNK_SIZE

        # Clamp window_size to actual remaining file bytes.
        # Without this, the Range header may request bytes beyond EOF,
        # causing Google Drive to return 416 Range Not Satisfiable.
        if window_offset + window_size > file_size:
            window_size = file_size - window_offset
            # Recalculate end_chunk for cache population
            end_chunk = (
                start_chunk + (window_size + CACHE_CHUNK_SIZE - 1) // CACHE_CHUNK_SIZE
            )

        log.debug(
            "Cache MISS '%s' chunk=%d — fetching %d-chunk window "
            "[chunks %d-%d, %d bytes at offset %d]",
            path,
            chunk_index,
            end_chunk - start_chunk,
            start_chunk,
            end_chunk - 1,
            window_size,
            window_offset,
        )

        fetch_start = time.time()

        try:
            # Fetch the full window in a single ranged request.
            buf = _stream_from_drive(
                self.sm.drive_service,
                drive_id,
                "",
                offset=window_offset,
                size=window_size,
                populate_cache=False,  # We'll populate cache manually
                service_pool=self._service_pool,
            )
            window_data = buf.read()
        except Exception as e:
            log.error(
                "Failed to read '%s' (window [%d, %d)) from Drive: %s",
                path,
                window_offset,
                window_offset + window_size,
                e,
            )
            raise FuseOSError(errno.EIO) from e

        # Write all contained chunks to the disk cache
        actual_size = len(window_data)
        num_chunks = (actual_size + CACHE_CHUNK_SIZE - 1) // CACHE_CHUNK_SIZE
        for i in range(num_chunks):
            chunk_start = i * CACHE_CHUNK_SIZE
            chunk_end = min(chunk_start + CACHE_CHUNK_SIZE, actual_size)
            if chunk_end > chunk_start:
                chunk_data = window_data[chunk_start:chunk_end]
                chunk_idx = start_chunk + i
                # Only cache non-empty chunks
                if len(chunk_data) > 0:
                    put_chunk(drive_id, chunk_idx, chunk_data)

        elapsed = time.time() - fetch_start
        log.info(
            "Fetched %d-chunk window for '%s' in %.2fs (%.1f MB, %.1f MB/s). "
            "Servicing chunk %d at offset %d.",
            end_chunk - start_chunk,
            path,
            elapsed,
            actual_size / (1024 * 1024),
            actual_size / (1024 * 1024) / max(elapsed, 0.001),
            chunk_index,
            chunk_offset,
        )

        # If the window data is significantly smaller than requested, log a warning
        if window_size > 0 and actual_size < window_size * 0.9:
            log.warning(
                "Window fetch returned only %d of %d requested bytes "
                "for '%s' (offset=%d). API may have per-request limit.",
                actual_size,
                window_size,
                path,
                window_offset,
            )

        # Extract the requested range from the window data
        relative_offset = (chunk_index - start_chunk) * CACHE_CHUNK_SIZE + chunk_offset
        if relative_offset + size <= actual_size:
            result = window_data[relative_offset : relative_offset + size]
        else:
            result = window_data[relative_offset:]

        # Trigger pre-fetch of the next window beyond this one
        try:
            self._trigger_prefetch(path, drive_id, file_size, end_chunk - 1)
        except Exception:
            log.debug("Prefetch trigger failed (non-fatal)", exc_info=True)

        return result

    # ---- File create / write ----

    def create(self, path, mode, fi=None):
        """Create a new empty file on Drive and return a file handle.

        No local file is created — the file exists only on Drive and in
        our in-memory dirty buffer until release().
        """
        full = self._full_path(path)
        name = os.path.basename(path)
        parent_local = os.path.dirname(full)

        parent_id = self.sm._get_drive_folder_id(parent_local, is_folder=True)
        if parent_id is None:
            raise FuseOSError(errno.EIO)

        # Create on Drive with empty content first
        from googleapiclient.http import MediaIoBaseUpload
        from googleapiclient.errors import HttpError

        body = {"name": name, "parents": [parent_id]}
        media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="application/octet-stream")
        try:
            with self.sm._drive_api_lock:
                drive_file = (
                    self.sm.drive_service.files()
                    .create(body=body, media_body=media, fields="id,name,mimeType,size")
                    .execute()
                )
        except HttpError as e:
            log.error("Failed to create Drive file '%s': %s", path, e)
            raise FuseOSError(errno.EIO) from e

        drive_id = drive_file["id"]

        # Record in mapping (thread-safe)
        with self.sm._mapping_lock:
            self.sm.local_file_info[full] = {
                "id": drive_id,
                "mode": "local",
                "mimeType": drive_file.get("mimeType", "application/octet-stream"),
                "size": drive_file.get("size", "0"),
                "last_accessed_time": time.time(),
            }
            self.sm.drive_id_to_local_path[drive_id] = full
        self.sm._save_mapping()

        # Set up an empty dirty buffer for this file handle
        fh = _allocate_fh()
        with _stream_buffers_lock:
            _stream_buffers[fh] = io.BytesIO()
            _dirty_buffers[fh] = io.BytesIO()
            _dirty_fh_to_path[fh] = full

        log.info("Created file '%s' (ID: %s) on Drive (fh=%d).", path, drive_id, fh)
        return fh

    def write(self, path, data, offset, fh):
        """Write *data* to the in-memory dirty buffer at *offset*.

        Data is accumulated in memory and uploaded to Drive on release().
        Nothing touches disk.
        """
        full = self._full_path(path)

        with _stream_buffers_lock:
            dirty = _dirty_buffers.get(fh)
            if dirty is None:
                dirty = io.BytesIO()
                _dirty_buffers[fh] = dirty
                _dirty_fh_to_path[fh] = full

        # Extend buffer if offset is beyond current length
        current_len = len(dirty.getvalue())
        if offset > current_len:
            dirty.seek(0, 2)
            dirty.write(b"\x00" * (offset - current_len))

        dirty.seek(offset)
        dirty.write(data)

        self._modified_paths.add(full)
        return len(data)

    def release(self, path, fh):
        """Called when file is closed.

        If the file was modified (write() was called), upload the dirty
        buffer content to Drive — but ONLY if the content actually changed
        (non-empty dirty buffer, and different from what's already on Drive).
        Read-only opens never trigger an upload.
        """
        full = self._full_path(path)
        info = self._info(path)
        if info is None or info.get("mimeType") == "application/vnd.google-apps.folder":
            return 0

        drive_id = info.get("id")
        if not drive_id:
            return 0

        # Upload if the file was modified via write()
        was_modified = full in self._modified_paths
        if was_modified:
            self._modified_paths.discard(full)

            with _stream_buffers_lock:
                dirty = _dirty_buffers.pop(fh, None)
                _dirty_fh_to_path.pop(fh, None)

            if dirty is not None:
                dirty_content = dirty.getvalue()

                # Only upload if the content is non-empty
                if len(dirty_content) == 0:
                    log.info(
                        "Skipping upload of '%s' — dirty buffer is empty "
                        "(editor may have opened with truncation, "
                        "no actual write occurred).",
                        path,
                    )
                else:
                    try:
                        log.info(
                            "Uploading '%s' to Drive on close (%d bytes)...",
                            path,
                            len(dirty_content),
                        )
                        from googleapiclient.http import MediaIoBaseUpload

                        media = MediaIoBaseUpload(
                            io.BytesIO(dirty_content),
                            mimetype="application/octet-stream",
                            resumable=True,
                        )
                        with self.sm._drive_api_lock:
                            updated = (
                                self.sm.drive_service.files()
                                .update(
                                    fileId=drive_id,
                                    media_body=media,
                                    fields="id,name,mimeType,size",
                                )
                                .execute()
                            )

                        # Update mapping with new size (thread-safe)
                        with self.sm._mapping_lock:
                            if full in self.sm.local_file_info:
                                self.sm.local_file_info[full]["size"] = updated.get(
                                    "size", "0"
                                )
                                self.sm.local_file_info[full]["mimeType"] = updated.get(
                                    "mimeType", "application/octet-stream"
                                )
                        self.sm._save_mapping()

                        log.info("Uploaded '%s' (ID: %s).", path, drive_id)
                    except Exception as e:
                        log.error("Failed to upload '%s' on close: %s", path, e)

        # Invalidate disk cache if file was modified (use captured flag since
        # _modified_paths was already cleared above)
        if was_modified:
            invalidate_file(drive_id)

        # Discard any cached state for this file handle
        with _stream_buffers_lock:
            _stream_buffers.pop(fh, None)  # Google Docs export cache
            _dirty_buffers.pop(fh, None)
            _dirty_fh_to_path.pop(fh, None)

        return 0

    def truncate(self, path, length, fh=None):
        """Truncate file to *length* bytes in the in-memory buffer.

        If no dirty buffer exists (read-only open), create one so that
        subsequent write() calls work correctly. Editors commonly call
        truncate(0) followed by write() to replace file content.
        """
        full = self._full_path(path)
        if fh is not None:
            with _stream_buffers_lock:
                buf = _dirty_buffers.get(fh)
                if buf is None:
                    # Read-only open — create dirty buffer on demand so
                    # truncate+write works (e.g. editors replacing content).
                    buf = io.BytesIO()
                    _dirty_buffers[fh] = buf
                    _dirty_fh_to_path[fh] = full
                current = buf.getvalue()
                if length < len(current):
                    # Truncate
                    buf.seek(0)
                    buf.truncate(length)
                elif length > len(current):
                    # Extend with null bytes
                    buf.seek(0, 2)
                    buf.write(b"\x00" * (length - len(current)))
            self._modified_paths.add(full)
        return 0

    # ---- Directory operations ----

    def mkdir(self, path, mode):
        """Create a directory on Drive.

        Directories starting with ``.Trash`` (file manager trash folders)
        are silently ignored — they are transient file-manager artifacts
        that should not be propagated to Google Drive.
        """
        basename = os.path.basename(path.rstrip("/"))
        if basename.startswith(".Trash"):
            log.info("Ignoring trash folder creation: '%s'", path)
            return
        full = self._full_path(path)
        self.sm.create_folder(full)
        log.info("Created directory '%s'.", path)

    def rmdir(self, path):
        """Block directory deletion and notify the user."""
        self._notify(
            f"Deletion blocked: '{path}' is on Google Drive and "
            "would be permanently deleted. Use drive.google.com to manage files."
        )
        log.warning("Blocked directory deletion: %s", path)
        raise FuseOSError(errno.EACCES)

    # ---- Delete / rename ----

    def unlink(self, path):
        """Delete a file from Google Drive.

        Locally-created files (lock files, temp files created by editors)
        are deleted from Drive automatically. Files that originated from
        Drive (mode != "local") are blocked and the user is warned, since
        the deletion would be permanent.
        """
        info = self._info(path)
        if info is None:
            raise FuseOSError(errno.ENOENT)

        # Allow deletion of locally-created files (e.g. .~lock.*, temp files)
        if info.get("mode") == "local":
            full = self._full_path(path)
            self.sm.delete_file(full)
            log.info("Deleted locally-created file '%s' from Drive.", path)
            return

        # Block deletion of Drive-original files
        self._notify(
            f"Deletion blocked: '{path}' is on Google Drive and "
            "would be permanently deleted. Use drive.google.com to manage files."
        )
        log.warning("Blocked file deletion: %s", path)
        raise FuseOSError(errno.EACCES)

    def rename(self, old, new):
        """Rename/move a file or directory on Drive."""
        old_full = self._full_path(old)
        new_full = self._full_path(new)

        self.sm.move_item(old_full, new_full)
        log.info("Renamed '%s' -> '%s'.", old, new)

    # ---- Symlinks / special (unsupported) ----

    def symlink(self, target, source):
        raise FuseOSError(errno.ENOTSUP)

    def link(self, target, source):
        raise FuseOSError(errno.ENOTSUP)

    def readlink(self, path):
        raise FuseOSError(errno.ENOTSUP)

    # ---- Chmod / chown (no-ops) ----

    def chmod(self, path, mode):
        return 0

    def chown(self, path, uid, gid):
        return 0

    def utimens(self, path, times=None):
        return 0

    # ---- Extended attributes (unsupported) ----

    def getxattr(self, path, name, position=0):
        raise FuseOSError(errno.ENOTSUP)

    def listxattr(self, path):
        return []

    # ---- Flush / fsync ----

    def flush(self, path, fh):
        return 0

    def fsync(self, path, datasync, fh):
        return 0

    def fsyncdir(self, path, datasync, fh):
        return 0
