"""Tests for the disk cache module."""

import os
import json
import time
import shutil
import tempfile
import threading
from unittest.mock import patch

import pytest

import disk_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def temp_cache_dir(monkeypatch):
    """Replace CACHE_DIR and _INDEX_FILE with temp paths for each test."""
    tmpdir = tempfile.mkdtemp()
    monkeypatch.setattr(disk_cache, "CACHE_DIR", tmpdir)
    monkeypatch.setattr(disk_cache, "_INDEX_FILE", os.path.join(tmpdir, "index.json"))
    # Reset global state
    monkeypatch.setattr(disk_cache, "_index", {})
    monkeypatch.setattr(disk_cache, "_index_lock", threading.Lock())
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Index tests
# ---------------------------------------------------------------------------


class TestIndex:
    def test_load_index_missing_file(self):
        """Loading with no index file should result in empty index."""
        disk_cache._load_index()
        assert disk_cache._index == {}

    def test_save_and_load_index(self):
        """Index should persist to disk and reload correctly."""
        disk_cache._index["drive1"] = {0: {"mtime": 100.0, "size": 4096}}
        disk_cache._save_index()

        disk_cache._index.clear()
        disk_cache._load_index()
        assert "drive1" in disk_cache._index
        assert disk_cache._index["drive1"][0]["mtime"] == 100.0
        assert disk_cache._index["drive1"][0]["size"] == 4096

    def test_save_and_load_multiple_chunks(self):
        """Multiple chunks per file should survive serialize/deserialize."""
        disk_cache._index["drive1"] = {
            0: {"mtime": 100.0, "size": 4096},
            1: {"mtime": 200.0, "size": 8192},
        }
        disk_cache._index["drive2"] = {5: {"mtime": 300.0, "size": 1024}}
        disk_cache._save_index()

        disk_cache._index.clear()
        disk_cache._load_index()
        assert len(disk_cache._index["drive1"]) == 2
        assert disk_cache._index["drive2"][5]["size"] == 1024

    def test_index_empty_after_clear(self):
        """Clearing the index should result in empty index file."""
        disk_cache._index["drive1"] = {0: {"mtime": 100.0, "size": 4096}}
        disk_cache._save_index()
        disk_cache._index.clear()
        disk_cache._save_index()

        disk_cache._index.clear()
        disk_cache._load_index()
        assert disk_cache._index == {}


# ---------------------------------------------------------------------------
# Chunk path tests
# ---------------------------------------------------------------------------


class TestChunkPath:
    def test_chunk_path_format(self):
        """Chunk path should use hex formatting."""
        path = disk_cache.get_chunk_path("drive1", 0)
        assert path.endswith("/00000000")

        path = disk_cache.get_chunk_path("drive1", 255)
        assert path.endswith("/000000ff")

        path = disk_cache.get_chunk_path("drive1", 4096)
        assert path.endswith("/00001000")

    def test_chunk_path_uses_cache_dir(self):
        """Chunk path should be under CACHE_DIR."""
        path = disk_cache.get_chunk_path("drive1", 0)
        assert path.startswith(disk_cache.CACHE_DIR)
        assert "drive1" in path


# ---------------------------------------------------------------------------
# put_chunk / get_chunk / has_chunk tests
# ---------------------------------------------------------------------------


class TestChunkIO:
    def test_put_and_get_chunk(self):
        """Writing a chunk and reading it back should return same data."""
        data = b"hello world"
        disk_cache.put_chunk("drive1", 0, data)
        assert disk_cache.has_chunk("drive1", 0)
        result = disk_cache.get_chunk("drive1", 0)
        assert result == data

    def test_get_missing_chunk(self):
        """Getting a chunk that was never written should return None."""
        assert disk_cache.get_chunk("nonexistent", 0) is None

    def test_has_chunk_missing(self):
        """has_chunk should return False for missing chunks."""
        assert not disk_cache.has_chunk("nonexistent", 0)

    def test_multiple_chunks_per_file(self):
        """Multiple chunks for the same file should all be retrievable."""
        disk_cache.put_chunk("drive1", 0, b"chunk0")
        disk_cache.put_chunk("drive1", 1, b"chunk1")
        disk_cache.put_chunk("drive1", 2, b"chunk2")

        assert disk_cache.get_chunk("drive1", 0) == b"chunk0"
        assert disk_cache.get_chunk("drive1", 1) == b"chunk1"
        assert disk_cache.get_chunk("drive1", 2) == b"chunk2"

    def test_chunk_with_large_data(self):
        """Large data should be stored and retrieved correctly."""
        data = os.urandom(1024 * 1024)  # 1 MB
        disk_cache.put_chunk("drive1", 0, data)
        result = disk_cache.get_chunk("drive1", 0)
        assert result == data
        assert len(result) == len(data)

    def test_get_chunk_after_disk_delete_returns_none(self):
        """If the file is deleted from disk, get_chunk should return None and update index."""
        disk_cache.put_chunk("drive1", 0, b"test")
        assert disk_cache.has_chunk("drive1", 0)

        os.remove(disk_cache.get_chunk_path("drive1", 0))

        result = disk_cache.get_chunk("drive1", 0)
        assert result is None
        assert not disk_cache.has_chunk("drive1", 0)

    def test_put_chunk_overwrites_existing(self):
        """Putting a chunk with the same index should overwrite."""
        disk_cache.put_chunk("drive1", 0, b"old")
        disk_cache.put_chunk("drive1", 0, b"new")
        assert disk_cache.get_chunk("drive1", 0) == b"new"

    def test_data_integrity(self):
        """Binary data should be byte-identical after round-trip."""
        original = bytes(range(256))
        disk_cache.put_chunk("drive1", 0, original)
        result = disk_cache.get_chunk("drive1", 0)
        assert result == original


# ---------------------------------------------------------------------------
# invalidate_file tests
# ---------------------------------------------------------------------------


class TestInvalidateFile:
    def test_invalidate_removes_all_chunks(self):
        """Invalidating a file should remove all its chunks from index and disk."""
        disk_cache.put_chunk("drive1", 0, b"data0")
        disk_cache.put_chunk("drive1", 1, b"data1")
        disk_cache.put_chunk("drive1", 2, b"data2")
        assert disk_cache.has_chunk("drive1", 0)
        assert disk_cache.has_chunk("drive1", 1)
        assert disk_cache.has_chunk("drive1", 2)

        disk_cache.invalidate_file("drive1")

        assert not disk_cache.has_chunk("drive1", 0)
        assert not disk_cache.has_chunk("drive1", 1)
        assert not disk_cache.has_chunk("drive1", 2)

        chunk_dir = os.path.dirname(disk_cache.get_chunk_path("drive1", 0))
        assert not os.path.isdir(chunk_dir)

    def test_invalidate_other_files_untouched(self):
        """Invalidating one file should not affect other files."""
        disk_cache.put_chunk("drive1", 0, b"drive1")
        disk_cache.put_chunk("drive2", 0, b"drive2")
        disk_cache.invalidate_file("drive1")

        assert not disk_cache.has_chunk("drive1", 0)
        assert disk_cache.has_chunk("drive2", 0)
        assert disk_cache.get_chunk("drive2", 0) == b"drive2"

    def test_invalidate_nonexistent_file(self):
        """Invalidating a file that doesn't exist should not raise."""
        disk_cache.invalidate_file("nonexistent")


# ---------------------------------------------------------------------------
# get_cache_size / clear_cache tests
# ---------------------------------------------------------------------------


class TestCacheSize:
    def test_cache_size_empty(self):
        """Empty cache should report size 0."""
        assert disk_cache.get_cache_size() == 0

    def test_cache_size_after_put(self):
        """Cache size should reflect written data."""
        disk_cache.put_chunk("drive1", 0, b"12345")
        assert disk_cache.get_cache_size() == 5

    def test_cache_size_multiple_chunks(self):
        """Cache size should sum across files and chunks."""
        disk_cache.put_chunk("drive1", 0, b"a" * 1000)
        disk_cache.put_chunk("drive1", 1, b"b" * 2000)
        disk_cache.put_chunk("drive2", 0, b"c" * 500)
        assert disk_cache.get_cache_size() == 3500


class TestClearCache:
    def test_clear_cache_empties_everything(self):
        """Clearing cache should remove all chunks and reset index."""
        disk_cache.put_chunk("drive1", 0, b"data")
        disk_cache.put_chunk("drive2", 5, b"more")
        freed = disk_cache.clear_cache()
        assert freed > 0
        assert disk_cache.get_cache_size() == 0
        assert not disk_cache.has_chunk("drive1", 0)
        assert not disk_cache.has_chunk("drive2", 5)

    def test_clear_cache_returns_freed_bytes(self):
        """clear_cache should return the number of bytes freed."""
        disk_cache.put_chunk("drive1", 0, b"a" * 1000)
        freed = disk_cache.clear_cache()
        assert freed == 1000

    def test_clear_cache_empty(self):
        """Clearing an already empty cache should not raise."""
        freed = disk_cache.clear_cache()
        assert freed == 0


# ---------------------------------------------------------------------------
# CacheCleanupThread tests
# ---------------------------------------------------------------------------


class TestCacheCleanupThread:
    def test_cleanup_under_limit_no_eviction(self):
        """When cache is under the size limit, cleanup should not evict."""
        disk_cache.put_chunk("drive1", 0, b"a" * 100)

        stop_event = threading.Event()
        thread = disk_cache.CacheCleanupThread(stop_event)
        thread._cleanup()

        assert disk_cache.has_chunk("drive1", 0)

    def test_cleanup_evicts_oldest_chunks(self):
        """When over limit, cleanup should evict oldest chunks first."""
        with patch("disk_cache.CACHE_MAX_SIZE_MB", 0.001):
            disk_cache.put_chunk("drive1", 0, b"a" * 512)
            time.sleep(0.01)
            disk_cache.put_chunk("drive1", 1, b"b" * 512)
            time.sleep(0.01)
            disk_cache.put_chunk("drive1", 2, b"c" * 512)

            stop_event = threading.Event()
            thread = disk_cache.CacheCleanupThread(stop_event)
            thread._cleanup()

            cached_indices = [i for i in range(3) if disk_cache.has_chunk("drive1", i)]
            assert len(cached_indices) < 3

    def test_cleanup_evicts_expired_chunks(self):
        """Chunks older than CACHE_MAX_AGE_SECONDS should be evicted."""
        with (
            patch("disk_cache.CACHE_MAX_AGE_SECONDS", 0),
            patch("disk_cache.CACHE_MAX_SIZE_MB", 0),
        ):
            disk_cache.put_chunk("drive1", 0, b"data")
            assert disk_cache.has_chunk("drive1", 0)

            stop_event = threading.Event()
            thread = disk_cache.CacheCleanupThread(stop_event)
            thread._cleanup()

            assert not disk_cache.has_chunk("drive1", 0)

    def test_cleanup_thread_runs_and_stops(self):
        """The cleanup thread should start, run, and stop cleanly."""
        stop_event = threading.Event()
        thread = disk_cache.CacheCleanupThread(stop_event)
        thread.start()
        assert thread.is_alive()

        stop_event.set()
        thread.join(timeout=2)
        assert not thread.is_alive()

    def test_cleanup_leaves_recent_chunks(self):
        """Recent chunks under limit should survive cleanup."""
        disk_cache.put_chunk("drive1", 0, b"recent")
        disk_cache.put_chunk("drive2", 0, b"also_recent")

        stop_event = threading.Event()
        thread = disk_cache.CacheCleanupThread(stop_event)
        thread._cleanup()

        assert disk_cache.has_chunk("drive1", 0)
        assert disk_cache.has_chunk("drive2", 0)

    def test_cleanup_evicts_drive_id_from_index(self):
        """When all chunks for a drive_id are evicted, it should vanish from index."""
        with patch("disk_cache.CACHE_MAX_SIZE_MB", 0.001):
            disk_cache.put_chunk("drive1", 0, b"a" * 2048)

            stop_event = threading.Event()
            thread = disk_cache.CacheCleanupThread(stop_event)
            thread._cleanup()

            assert "drive1" not in disk_cache._index


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_put_and_get(self):
        """Multiple threads should be able to put/get chunks concurrently."""
        import concurrent.futures

        def worker(worker_id):
            for i in range(20):
                disk_cache.put_chunk(
                    f"drive_{worker_id}", i, f"data_{worker_id}_{i}".encode()
                )
            return all(
                disk_cache.get_chunk(f"drive_{worker_id}", i) is not None
                for i in range(20)
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(8)]
            for future in concurrent.futures.as_completed(futures):
                assert future.result()

    def test_concurrent_invalidate_and_get(self):
        """Invalidate and get should not deadlock."""
        import concurrent.futures

        disk_cache.put_chunk("drive1", 0, b"data")

        def invalidater():
            for _ in range(50):
                disk_cache.invalidate_file("drive1")
                disk_cache.put_chunk("drive1", 0, b"data")

        def reader():
            for _ in range(50):
                disk_cache.get_chunk("drive1", 0)
                disk_cache.has_chunk("drive1", 0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(invalidater),
                executor.submit(reader),
                executor.submit(reader),
                executor.submit(reader),
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_data(self):
        """Empty bytes should be storable."""
        disk_cache.put_chunk("drive1", 0, b"")
        assert disk_cache.has_chunk("drive1", 0)
        assert disk_cache.get_chunk("drive1", 0) == b""

    def test_zero_length_chunk(self):
        """Zero-length data should not break index."""
        disk_cache.put_chunk("drive1", 0, b"")
        assert disk_cache.get_cache_size() == 0

    def test_large_chunk_index(self):
        """Large chunk indices should work fine."""
        disk_cache.put_chunk("drive1", 1000000, b"big_index")
        assert disk_cache.get_chunk("drive1", 1000000) == b"big_index"

    def test_special_characters_in_drive_id(self):
        """Drive IDs with special characters should be handled."""
        disk_cache.put_chunk("abc_123-xyz!@#", 0, b"special")
        assert disk_cache.get_chunk("abc_123-xyz!@#", 0) == b"special"

    def test_index_file_consistency(self):
        """Index file on disk should match in-memory index after operations."""
        disk_cache.put_chunk("drive1", 0, b"test")
        disk_cache._save_index()

        with open(disk_cache._INDEX_FILE) as f:
            saved = json.load(f)
        assert "drive1" in saved
        assert "0" in saved["drive1"]
