"""Tests for the authentication module."""

import os
import json
import tempfile
import shutil
from unittest.mock import patch, MagicMock

import pytest

import auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def temp_token_dir(monkeypatch):
    """Replace TOKEN_FILE path with a temp directory for each test."""
    tmpdir = tempfile.mkdtemp()
    token_path = os.path.join(tmpdir, "token.json")
    monkeypatch.setattr(auth, "TOKEN_FILE", token_path)
    yield tmpdir, token_path
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_token(token_path, data=None):
    """Write a mock token file."""
    if data is None:
        data = {"token": "test", "refresh_token": "test_refresh"}
    with open(token_path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# Test authenticate_google_drive
# ---------------------------------------------------------------------------


class TestAuthenticate:
    def test_returns_none_when_no_client_id(self, monkeypatch, temp_token_dir):
        """Without CLIENT_ID, authenticate should return None."""
        monkeypatch.setattr(auth, "CLIENT_ID", "")
        result = auth.authenticate_google_drive()
        assert result is None

    def test_returns_creds_when_token_exists_and_valid(
        self, monkeypatch, temp_token_dir
    ):
        """When a valid token exists, should return credentials without OAuth flow."""
        _, token_path = temp_token_dir
        monkeypatch.setattr(auth, "CLIENT_ID", "test-client-id")

        _write_token(token_path)

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False

        with patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ):
            result = auth.authenticate_google_drive()
            assert result is not None
            assert result.valid

    def test_refreshes_expired_token(self, monkeypatch, temp_token_dir):
        """When token is expired but has refresh_token, should refresh."""
        _, token_path = temp_token_dir
        monkeypatch.setattr(auth, "CLIENT_ID", "test-client-id")

        _write_token(token_path)

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "test_refresh"
        mock_creds.to_json.return_value = json.dumps({"token": "refreshed"})

        with patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ):
            with patch("google.auth.transport.requests.Request"):
                result = auth.authenticate_google_drive()
                assert result is not None

    def test_saves_token_after_new_auth(self, monkeypatch, temp_token_dir):
        """After successful OAuth, the token should be saved to disk."""
        _, token_path = temp_token_dir
        monkeypatch.setattr(auth, "CLIENT_ID", "test-client-id")
        monkeypatch.setattr(auth, "CLIENT_SECRET", "test-secret")

        mock_creds = MagicMock()
        mock_creds.to_json.return_value = json.dumps({"token": "new_token"})

        with patch("auth._run_oauth_flow", return_value=mock_creds):
            result = auth.authenticate_google_drive()
            assert result is not None
            assert os.path.exists(token_path)

    def test_runs_oauth_flow_when_no_token(self, monkeypatch, temp_token_dir):
        """With no existing token, should run OAuth flow."""
        _, token_path = temp_token_dir
        monkeypatch.setattr(auth, "CLIENT_ID", "test-client-id")
        monkeypatch.setattr(auth, "CLIENT_SECRET", "test-secret")

        assert not os.path.exists(token_path)

        mock_creds = MagicMock()
        mock_creds.to_json.return_value = json.dumps({"token": "new"})

        with patch("auth._run_oauth_flow", return_value=mock_creds):
            result = auth.authenticate_google_drive()
            assert result is not None


class TestReauthenticate:
    def test_deletes_old_token(self, monkeypatch, temp_token_dir):
        """Reauthentication should delete the old token file."""
        _, token_path = temp_token_dir
        monkeypatch.setattr(auth, "CLIENT_ID", "test-client-id")

        _write_token(token_path, {"token": "old"})
        assert os.path.exists(token_path)

        mock_creds = MagicMock()
        mock_creds.to_json.return_value = json.dumps({"token": "new"})

        with patch("auth._run_oauth_flow", return_value=mock_creds):
            auth.reauthenticate_google_drive()

        with open(token_path) as f:
            content = json.load(f)
        assert content["token"] == "new"

    def test_returns_creds_on_success(self, monkeypatch, temp_token_dir):
        """Reauthentication should return new credentials."""
        monkeypatch.setattr(auth, "CLIENT_ID", "test-client-id")
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = json.dumps({"token": "new"})

        with patch("auth._run_oauth_flow", return_value=mock_creds):
            result = auth.reauthenticate_google_drive()
            assert result is not None

    def test_returns_none_on_failure(self, monkeypatch, temp_token_dir):
        """Reauthentication should return None if OAuth fails."""
        monkeypatch.setattr(auth, "CLIENT_ID", "test-client-id")

        with patch("auth._run_oauth_flow", return_value=None):
            result = auth.reauthenticate_google_drive()
            assert result is None


class TestGetUserEmail:
    def test_returns_email_on_success(self):
        """get_user_email should return the email from userinfo."""
        mock_creds = MagicMock()
        mock_service = MagicMock()
        mock_userinfo = MagicMock()
        mock_userinfo.get().execute.return_value = {"email": "test@example.com"}
        mock_service.userinfo.return_value = mock_userinfo

        with patch("googleapiclient.discovery.build", return_value=mock_service):
            email = auth.get_user_email(mock_creds)
            assert email == "test@example.com"

    def test_returns_none_on_failure(self):
        """get_user_email should return None if the API call fails."""
        mock_creds = MagicMock()

        with patch(
            "googleapiclient.discovery.build", side_effect=Exception("API error")
        ):
            email = auth.get_user_email(mock_creds)
            assert email is None


class TestLogout:
    def test_logout_deletes_token_file(self, temp_token_dir):
        """Logout should delete the token file."""
        _, token_path = temp_token_dir
        _write_token(token_path)
        assert os.path.exists(token_path)

        result = auth.logout_google_account()
        assert result
        assert not os.path.exists(token_path)

    def test_logout_returns_false_if_no_token(self, temp_token_dir):
        """Logout should return False if no token file exists."""
        _, token_path = temp_token_dir
        assert not os.path.exists(token_path)
        result = auth.logout_google_account()
        assert not result
