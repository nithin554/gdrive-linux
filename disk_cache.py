"""Local disk cache for Google Drive file chunks.

Files are stored on disk in 4 MB chunks under ``CACHE_DIR / <drive_id> /``
with filenames like ``00000000`` (chunk 0), ``00000001`` (chunk 1), etc.
An LRU eviction policy keeps total cache size under ``CACHE_MAX_SIZE_MB``.
A background cleanup thread runs periodically to evict stale entries.

The cache is optional — if a chunk is missing (evicted or never fetched),
the caller falls through to a network request.
"""

import os
import time
import logging
import threading

from config import (
    CACHE_DIR,
    CACHE_MAX_SIZE_MB,
    CACHE_MAX_AGE_SECONDS,
    CACHE_CLEANUP_INTERVAL,
)

log = logging.getLogger(__name__)

# Cache index file: stores {drive_id: {chunk_index: {"mtime": float, "size": int}}}
_INDEX_FILE = os.path.join(CACHE_DIR, "index.json")
_index_lock = threading.Lock()
_index: dict[str, dict[int, dict]] = {}


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------


def _load_index():
    global _index
    try:
        import json

        with open(_INDEX_FILE) as f:
            _index = json.load(f)
        # Convert string keys back to int for chunk indices
        cleaned = {}
        for drive_id, chunks in _index.items():
            cleaned[drive_id] = {int(k): v for k, v in chunks.items()}
        _index = cleaned
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        _index = {}


def _save_index():
    import json

    os.makedirs(CACHE_DIR, exist_ok=True)
    # Convert int keys to strings for JSON
    serializable = {}
    for drive_id, chunks in _index.items():
        serializable[drive_id] = {str(k): v for k, v in chunks.items()}
    with open(_INDEX_FILE, "w") as f:
        json.dump(serializable, f, indent=2)


def _touch_chunk(drive_id: str, chunk_index: int):
    """Update access time for a cached chunk."""
    with _index_lock:
        if drive_id in _index and chunk_index in _index[drive_id]:
            _index[drive_id][chunk_index]["mtime"] = time.time()
            _save_index()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_chunk_path(drive_id: str, chunk_index: int) -> str:
    """Return the on-disk path for a given file chunk."""
    return os.path.join(CACHE_DIR, drive_id, f"{chunk_index:08x}")


def has_chunk(drive_id: str, chunk_index: int) -> bool:
    """Check if a chunk exists in the cache."""
    with _index_lock:
        return drive_id in _index and chunk_index in _index[drive_id]


def get_chunk(drive_id: str, chunk_index: int) -> bytes | None:
    """Read a chunk from the cache. Returns None if missing."""
    if not has_chunk(drive_id, chunk_index):
        return None
    path = get_chunk_path(drive_id, chunk_index)
    try:
        with open(path, "rb") as f:
            data = f.read()
        _touch_chunk(drive_id, chunk_index)
        return data
    except (FileNotFoundError, OSError) as e:
        log.warning("Cache read error for %s chunk %d: %s", drive_id, chunk_index, e)
        # Remove from index so we don't keep trying
        with _index_lock:
            _index.get(drive_id, {}).pop(chunk_index, None)
            if drive_id in _index and not _index[drive_id]:
                del _index[drive_id]
            _save_index()
        return None


def put_chunk(drive_id: str, chunk_index: int, data: bytes):
    """Write a chunk to the cache."""
    chunk_dir = os.path.join(CACHE_DIR, drive_id)
    os.makedirs(chunk_dir, exist_ok=True)
    path = get_chunk_path(drive_id, chunk_index)
    try:
        with open(path, "wb") as f:
            f.write(data)
        with _index_lock:
            _index.setdefault(drive_id, {})[chunk_index] = {
                "mtime": time.time(),
                "size": len(data),
            }
            _save_index()
    except OSError as e:
        log.warning("Cache write error for %s chunk %d: %s", drive_id, chunk_index, e)


def invalidate_file(drive_id: str):
    """Remove all cached chunks for a file (e.g. after remote update)."""
    with _index_lock:
        chunks = _index.pop(drive_id, {})
        if chunks:
            _save_index()
    # Remove files from disk
    chunk_dir = os.path.join(CACHE_DIR, drive_id)
    if os.path.isdir(chunk_dir):
        import shutil

        shutil.rmtree(chunk_dir, ignore_errors=True)
        log.info("Invalidated cache for drive_id=%s (%d chunks)", drive_id, len(chunks))


def get_cache_size() -> int:
    """Return total cache size in bytes (from index, no disk scan)."""
    total = 0
    with _index_lock:
        for drive_id, chunks in _index.items():
            for info in chunks.values():
                total += info.get("size", 0)
    return total


def clear_cache() -> int:
    """Remove ALL cached chunks and reset the index.

    Returns the number of bytes that were freed.
    """
    import shutil

    with _index_lock:
        freed = sum(
            info.get("size", 0)
            for chunks in _index.values()
            for info in chunks.values()
        )
        _index.clear()
        _save_index()

    # Remove all cache directories
    if os.path.isdir(CACHE_DIR):
        for entry in os.listdir(CACHE_DIR):
            path = os.path.join(CACHE_DIR, entry)
            if entry == "index.json":
                continue
            try:
                if os.path.isfile(path):
                    os.remove(path)
                else:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError as e:
                log.warning("Failed to remove cache entry '%s': %s", entry, e)

    log.info("Cleared entire cache (%d bytes freed).", freed)
    return freed


# ---------------------------------------------------------------------------
# Background cleanup thread
# ---------------------------------------------------------------------------


class CacheCleanupThread(threading.Thread):
    """Periodically evicts stale chunks to keep cache under the size limit.

    Eviction order:
    1. Chunks older than ``CACHE_MAX_AGE_SECONDS`` are removed first.
    2. If still over the limit, remaining chunks are removed in LRU order
       (oldest ``mtime`` first) until the cache fits under the limit.
    """

    def __init__(self, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self._cleanup_count = 0

    def run(self):
        _load_index()
        log.info(
            "Cache cleanup thread started (max %d MB, interval %ds).",
            CACHE_MAX_SIZE_MB,
            CACHE_CLEANUP_INTERVAL,
        )
        while not self.stop_event.is_set():
            self._cleanup()
            self.stop_event.wait(CACHE_CLEANUP_INTERVAL)
        log.info("Cache cleanup thread stopped.")

    def _cleanup(self):
        self._cleanup_count += 1
        max_bytes = CACHE_MAX_SIZE_MB * 1024 * 1024
        now = time.time()

        with _index_lock:
            # Collect all chunks with their metadata
            entries: list[
                tuple[str, int, float, int]
            ] = []  # (drive_id, chunk_idx, mtime, size)
            for drive_id, chunks in list(_index.items()):
                for chunk_idx, info in list(chunks.items()):
                    mtime = info.get("mtime", 0)
                    size = info.get("size", 0)
                    entries.append((drive_id, chunk_idx, mtime, size))

            total_size = sum(e[3] for e in entries)
            if total_size <= max_bytes:
                return  # Under limit, nothing to do

            # Sort by mtime ascending (oldest first) for LRU eviction
            entries.sort(key=lambda e: e[2])

            # First pass: evict anything past max age
            for drive_id, chunk_idx, mtime, size in entries:
                if now - mtime > CACHE_MAX_AGE_SECONDS:
                    _evict_chunk(drive_id, chunk_idx)
                    total_size -= size
                    if total_size <= max_bytes:
                        return

            # Second pass: evict oldest chunks until under limit
            # Re-scan remaining entries
            remaining = []
            for drive_id, chunks in list(_index.items()):
                for chunk_idx, info in list(chunks.items()):
                    mtime = info.get("mtime", 0)
                    size = info.get("size", 0)
                    remaining.append((drive_id, chunk_idx, mtime, size))
            remaining.sort(key=lambda e: e[2])

            for drive_id, chunk_idx, mtime, size in remaining:
                _evict_chunk(drive_id, chunk_idx)
                total_size -= size
                if total_size <= max_bytes:
                    break

            _save_index()


def _evict_chunk(drive_id: str, chunk_index: int):
    """Remove a single chunk from cache (index + disk)."""
    # Remove from index
    _index.get(drive_id, {}).pop(chunk_index, None)
    if drive_id in _index and not _index[drive_id]:
        del _index[drive_id]

    # Remove from disk
    path = get_chunk_path(drive_id, chunk_index)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Failed to remove cached chunk %s/%s: %s", drive_id, chunk_index, e)

    # Clean up empty directory
    chunk_dir = os.path.join(CACHE_DIR, drive_id)
    try:
        if os.path.isdir(chunk_dir) and not os.listdir(chunk_dir):
            os.rmdir(chunk_dir)
    except OSError:
        pass
