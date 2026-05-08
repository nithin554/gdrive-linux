import os
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import SCOPES, TOKEN_FILE, CLIENT_ID, CLIENT_SECRET


def authenticate_google_drive():
    """Authenticates with Google Drive using user-provided client credentials.
    Uses PKCE flow (no client_secret needed on wire).
    Credentials are saved to token.json on first run.
    """
    if not CLIENT_ID:
        logging.error(
            "No Google OAuth CLIENT_ID configured.\n\n"
            "This is a build-time configuration issue. The application maintainer\n"
            "needs to set GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET environment\n"
            "variables, or inject them via GitHub repository secrets during CI build.\n\n"
            "See: https://console.cloud.google.com/apis/credentials\n"
        )
        return None

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": ["http://localhost"],
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return creds


def get_user_email(creds):
    """Fetches the authenticated user's email address."""
    try:
        from googleapiclient.discovery import build

        service = build("oauth2", "v2", credentials=creds)
        user_info = service.userinfo().get().execute()
        return user_info.get("email")
    except Exception as e:
        logging.error(f"Error fetching user email: {e}")
        return None


def logout_google_account():
    """Deletes the stored token.json file to log out the user."""
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
        logging.info("Google account token deleted.")
        return True
    return False
