"""Tests for the autostart module."""

import os
import shutil
import tempfile

import pytest

import autostart

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def temp_autostart_dir(monkeypatch):
    """Replace AUTOSTART_DIR with a temp directory for each test."""
    tmpdir = tempfile.mkdtemp()
    test_autostart = os.path.join(tmpdir, "autostart")
    test_desktop = os.path.join(test_autostart, "gdrive-linux.desktop")
    monkeypatch.setattr(autostart, "AUTOSTART_DIR", test_autostart)
    monkeypatch.setattr(autostart, "DESKTOP_FILE", test_desktop)
    yield tmpdir, test_autostart, test_desktop
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAutostart:
    def test_is_autostart_enabled_returns_false_by_default(self, temp_autostart_dir):
        """Autostart should not be enabled when no .desktop file exists."""
        assert not autostart.is_autostart_enabled()

    def test_enable_autostart_creates_desktop_file(self, temp_autostart_dir):
        """Enabling autostart should create the .desktop file."""
        _, _, desktop_file = temp_autostart_dir
        result = autostart.enable_autostart()
        assert result
        assert os.path.isfile(desktop_file)

    def test_enable_autostart_writes_correct_content(self, temp_autostart_dir):
        """The .desktop file should contain the expected content."""
        _, _, desktop_file = temp_autostart_dir
        autostart.enable_autostart()
        with open(desktop_file) as f:
            content = f.read()
        assert content == autostart.DESKTOP_CONTENT

    def test_enable_autostart_makes_it_visible(self, temp_autostart_dir):
        """After enabling, is_autostart_enabled should return True."""
        autostart.enable_autostart()
        assert autostart.is_autostart_enabled()

    def test_disable_autostart_removes_desktop_file(self, temp_autostart_dir):
        """Disabling autostart should remove the .desktop file."""
        _, _, desktop_file = temp_autostart_dir
        autostart.enable_autostart()
        assert os.path.isfile(desktop_file)

        result = autostart.disable_autostart()
        assert result
        assert not os.path.isfile(desktop_file)

    def test_disable_autostart_makes_it_invisible(self, temp_autostart_dir):
        """After disabling, is_autostart_enabled should return False."""
        autostart.enable_autostart()
        autostart.disable_autostart()
        assert not autostart.is_autostart_enabled()

    def test_disable_autostart_when_not_enabled(self, temp_autostart_dir):
        """Disabling when already disabled should return False (not an error)."""
        result = autostart.disable_autostart()
        assert not result

    def test_enable_twice(self, temp_autostart_dir):
        """Enabling twice should succeed and not raise."""
        assert autostart.enable_autostart()
        assert autostart.enable_autostart()
        assert autostart.is_autostart_enabled()

    def test_disable_twice(self, temp_autostart_dir):
        """Disabling twice should work (second call returns False)."""
        autostart.enable_autostart()
        assert autostart.disable_autostart()
        assert not autostart.disable_autostart()

    def test_enable_creates_autostart_directory(self, temp_autostart_dir):
        """The autostart directory should be created if it doesn't exist."""
        _, autostart_dir, _ = temp_autostart_dir
        assert not os.path.isdir(autostart_dir)
        autostart.enable_autostart()
        assert os.path.isdir(autostart_dir)

    def test_desktop_file_permissions(self, temp_autostart_dir):
        """The .desktop file should be a regular file."""
        _, _, desktop_file = temp_autostart_dir
        autostart.enable_autostart()
        assert os.path.isfile(desktop_file)

    def test_default_content_structure(self, temp_autostart_dir):
        """The desktop file content should have required fields."""
        assert "[Desktop Entry]" in autostart.DESKTOP_CONTENT
        assert "Type=Application" in autostart.DESKTOP_CONTENT
        assert "Name=gdrive-linux" in autostart.DESKTOP_CONTENT
        assert "Exec=gdrive-linux" in autostart.DESKTOP_CONTENT
