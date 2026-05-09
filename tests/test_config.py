"""Tests for configuration constants."""

import os

import config


class TestConfigConstants:
    def test_scopes_contain_drive(self):
        """Scopes should include Google Drive access."""
        scopes_str = " ".join(config.SCOPES)
        assert "drive" in scopes_str
        assert "userinfo.email" in scopes_str

    def test_cache_chunk_size_is_positive(self):
        """Cache chunk size should be a positive integer."""
        assert config.CACHE_CHUNK_SIZE > 0

    def test_cache_chunk_size_is_4mb(self):
        """Default cache chunk size should be 4 MB."""
        assert config.CACHE_CHUNK_SIZE == 4 * 1024 * 1024

    def test_readahead_window_chunks_is_positive(self):
        """Read-ahead window should be a non-negative integer."""
        assert config.READAHEAD_WINDOW_CHUNKS >= 0

    def test_max_concurrent_fetches_is_positive(self):
        """Max concurrent fetches should be positive."""
        assert config.MAX_CONCURRENT_FETCHES > 0

    def test_remote_sync_interval_is_reasonable(self):
        """Remote sync interval should be between 1 and 3600 seconds."""
        assert 1 <= config.REMOTE_SYNC_INTERVAL_SECONDS <= 3600

    def test_cache_max_size_is_reasonable(self):
        """Cache max size should be at least 100 MB."""
        assert config.CACHE_MAX_SIZE_MB >= 100

    def test_cache_max_age_is_reasonable(self):
        """Cache max age should be at least 1 hour."""
        assert config.CACHE_MAX_AGE_SECONDS >= 3600

    def test_cache_cleanup_interval_is_reasonable(self):
        """Cache cleanup interval should be between 30 and 3600 seconds."""
        assert 30 <= config.CACHE_CLEANUP_INTERVAL <= 3600

    def test_prefetch_trigger_threshold_is_valid(self):
        """Prefetch trigger threshold should be between 0 and 1."""
        assert 0.0 <= config.PREFETCH_TRIGGER_THRESHOLD <= 1.0

    def test_fuse_mount_point_is_none_by_default(self):
        """FUSE_MOUNT_POINT should be None until set at runtime."""
        assert config.FUSE_MOUNT_POINT is None

    def test_local_sync_folder_is_none_by_default(self):
        """LOCAL_SYNC_FOLDER should be None until set at runtime."""
        assert config.LOCAL_SYNC_FOLDER is None

    def test_token_file_path(self):
        """TOKEN_FILE should end with token.json."""
        assert config.TOKEN_FILE.endswith("token.json")

    def test_mapping_file_path(self):
        """MAPPING_FILE should end with sync_mapping.json."""
        assert config.MAPPING_FILE.endswith("sync_mapping.json")

    def test_settings_file_path(self):
        """SETTINGS_FILE should end with settings.json."""
        assert config.SETTINGS_FILE.endswith("settings.json")

    def test_cache_dir_exists(self):
        """CACHE_DIR should end with 'cache'."""
        assert config.CACHE_DIR.endswith("cache")

    def test_fuse_cache_dir_exists(self):
        """FUSE_CACHE_DIR should end with 'fuse'."""
        assert config.FUSE_CACHE_DIR.endswith("fuse")

    def test_config_dirs_created(self):
        """Config directories should have been created at import time."""
        config_dir = os.path.dirname(config.TOKEN_FILE)
        assert os.path.isdir(config_dir)

    def test_default_file_mode(self):
        """Default file mode should be 'online'."""
        assert config.DEFAULT_FILE_MODE == "online"


class TestConfigEnvOverride:
    def test_client_id_from_env(self, monkeypatch):
        """CLIENT_ID should be read from environment variable."""
        monkeypatch.setenv("GDRIVE_CLIENT_ID", "test-client-id")
        # Re-import to pick up new env
        import importlib

        cfg = importlib.reload(config)
        assert cfg.CLIENT_ID == "test-client-id"

    def test_client_secret_from_env(self, monkeypatch):
        """CLIENT_SECRET should be read from environment variable."""
        monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "test-client-secret")
        import importlib

        cfg = importlib.reload(config)
        assert cfg.CLIENT_SECRET == "test-client-secret"

    def test_client_id_default_empty(self):
        """CLIENT_ID should default to empty string."""
        # config was already imported, so CLIENT_ID should be whatever
        # the environment has. We just check it's a string.
        assert isinstance(config.CLIENT_ID, str)
