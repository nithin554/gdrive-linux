"""Tests for the DriveServicePool."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from drive_service_pool import DriveServicePool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_creds():
    """Create mock credentials."""
    return MagicMock()


def _make_mock_service():
    """Create a fresh mock drive service instance."""
    svc = MagicMock()
    svc._http = MagicMock()
    return svc


@pytest.fixture
def pool(mock_creds):
    """Create a DriveServicePool with a mock credentials factory."""
    return DriveServicePool(lambda: mock_creds)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDriveServicePool:
    def test_get_returns_service(self, pool, mock_creds):
        """get() should return a Drive API service instance."""
        mock_svc = _make_mock_service()
        with patch("drive_service_pool.build", return_value=mock_svc):
            svc = pool.get()
            assert svc is not None
            assert svc is mock_svc

    def test_get_caches_on_same_thread(self, pool, mock_creds):
        """get() called twice on same thread should return cached service."""
        mock_svc = _make_mock_service()
        with patch("drive_service_pool.build", return_value=mock_svc):
            svc1 = pool.get()
            svc2 = pool.get()
            assert svc1 is svc2

    def test_different_threads_get_different_services(self, pool, mock_creds):
        """get() on different threads should return different instances."""
        services = []
        lock = threading.Lock()

        # Each call to build() returns a fresh service
        def side_effect(*args, **kwargs):
            return _make_mock_service()

        with patch("drive_service_pool.build", side_effect=side_effect):

            def get_service():
                svc = pool.get()
                with lock:
                    services.append(svc)

            threads = [threading.Thread(target=get_service) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(services) == 3
            assert len({id(s) for s in services}) == 3

    def test_get_returns_none_when_no_creds(self):
        """Without credentials, get() should return None."""
        pool = DriveServicePool(lambda: None)
        svc = pool.get()
        assert svc is None

    def test_get_returns_none_on_build_failure(self, mock_creds):
        """If build() raises, get() should return None."""
        with patch("drive_service_pool.build", side_effect=Exception("Build failed")):
            pool = DriveServicePool(lambda: mock_creds)
            svc = pool.get()
            assert svc is None

    def test_dispose_all_closes_connections(self, pool, mock_creds):
        """dispose_all() should close HTTP connections on all services."""
        mock_svc = _make_mock_service()
        with patch("drive_service_pool.build", return_value=mock_svc):
            pool.get()
            pool.dispose_all()
            mock_svc._http.close.assert_called_once()

    def test_dispose_all_clears_pool(self, pool, mock_creds):
        """After dispose_all, active_service_count should be 0."""
        mock_svc = _make_mock_service()
        with patch("drive_service_pool.build", return_value=mock_svc):
            pool.get()
            pool.dispose_all()
            assert pool.active_service_count == 0

    def test_dispose_all_handles_missing_http(self, pool, mock_creds):
        """dispose_all() should handle services without _http attribute."""
        mock_svc = MagicMock()
        # No _http attribute
        with patch("drive_service_pool.build", return_value=mock_svc):
            pool.get()
            pool.dispose_all()

    def test_dispose_all_handles_close_error(self, pool, mock_creds):
        """dispose_all() should handle errors during close()."""
        mock_svc = _make_mock_service()
        mock_svc._http.close.side_effect = Exception("Close failed")
        with patch("drive_service_pool.build", return_value=mock_svc):
            pool.get()
            pool.dispose_all()

    def test_active_service_count(self, pool, mock_creds):
        """active_service_count should reflect number of alive services."""
        mock_svc = _make_mock_service()
        with patch("drive_service_pool.build", return_value=mock_svc):
            assert pool.active_service_count == 0
            pool.get()
            assert pool.active_service_count >= 0

    def test_service_recreated_after_dispose(self, pool, mock_creds):
        """After dispose_all, get() should create a fresh service."""
        services_created = []

        def side_effect(*args, **kwargs):
            svc = _make_mock_service()
            services_created.append(svc)
            return svc

        with patch("drive_service_pool.build", side_effect=side_effect):
            svc1 = pool.get()
            pool.dispose_all()
            # Clear the thread-local cache so get() creates a new service
            pool._local.service = None
            svc2 = pool.get()
            assert svc1 is not svc2
            assert len(services_created) == 2
